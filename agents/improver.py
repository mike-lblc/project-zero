"""УЛУЧШАТЕЛЬ — агент, который правит код других агентов языковой моделью.

Владелец потребовал агента, способного вносить правки и улучшения в код
остальных. Он нужен, и вот как он устроен так, чтобы не стать опасным.

ПОЧЕМУ ЭТО НЕ НАРУШАЕТ ЗАПРЕТ НА СУЖДЕНИЕ. Протокол прямо относит «предлагать»
к суждениям, запрещённым локальной модели. Если бы модель сама решала, что в
системе плохо и что с этим делать, — это было бы суждение, и делать так
нельзя. Здесь роли разделены:

    ЧТО чинить   решает ДЕТЕКТОР — проверка, выведенная из реальной поломки
    КАК чинить   пишет МОДЕЛЬ — это изложение, а не выбор
    СТАЛО ЛИ ЛУЧШЕ решает АУДИТ — внешняя мера, к правке не причастная
    ЕСЛИ ХУЖЕ    откат, без обсуждения

Модель не выбирает цель и не оценивает результат. Она делает ровно одно:
переписывает названный кусок так, чтобы названный детектор перестал
срабатывать. Это проверяемо механически.

ЧЕГО УЛУЧШАТЕЛЬ НЕ ТРОГАЕТ НИКОГДА — и почему список именно такой:
  audit.py, fake_work_audit.py, core/regressions.py  — это судьи. Правка судьи
      ради прохождения проверки превращает аудит в украшение;
  core/guard.py, core/execution.py                   — это правила. Агент, сам
      себе меняющий права, называется неуправляемым;
  core/db.py                                          — потеря данных необратима;
  .env, .gitignore                                    — ключи и защита от их утечки;
  сам себя                                            — по очевидной причине.

ПОЧЕМУ ОТКАТ — ГЛАВНАЯ ЧАСТЬ. Правка, сделанная моделью, правдоподобна по
построению: она выглядит как исправление независимо от того, является ли им.
Отличить одно от другого может только внешняя мера ДО и ПОСЛЕ. Поэтому здесь
нет ветки «правка выглядит разумной, оставим»: либо аудит стал не хуже, либо
файл возвращается в прежний вид.
"""
import ast
import difflib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import guard, bus, router, telemetry  # noqa: E402

# Файлы, которых улучшатель не касается ни при каких условиях.
FORBIDDEN = {
    "audit.py", "fake_work_audit.py", "gnd_readiness.py", "mtbx_audit.py",
    "verify_claims.py", "agent_anatomy.py",
    "core/regressions.py", "core/guard.py", "core/execution.py", "core/db.py",
    "agents/improver.py", ".env", ".gitignore",
}

MAX_REGION = 80          # строк за раз: крупная правка непроверяема глазами
BACKUP = ROOT / "data" / "improver_backup"


def _forbidden(rel):
    rel = rel.replace("\\", "/")
    return rel in FORBIDDEN or Path(rel).name in FORBIDDEN


def _audit_score():
    """Внешняя мера качества: сколько проверок проходит прямо сейчас.

    Меряется ДО и ПОСЛЕ правки. Любое ухудшение — повод откатить, даже если
    правка выглядит безупречно: выглядеть правильно и быть правильным здесь
    не одно и то же.
    """
    scores = {}
    for name, args in (("аудит", ["audit.py"]),
                       ("подделка", ["fake_work_audit.py"]),
                       ("инварианты", ["core/regressions.py"])):
        try:
            r = subprocess.run([sys.executable, "-X", "utf8"] + args, cwd=str(ROOT),
                               capture_output=True, text=True, encoding="utf-8",
                               errors="ignore", timeout=900)
            line = next((l for l in (r.stdout or "").splitlines()
                         if l.startswith("ИТОГ")), "")
            import re
            ok = int(re.search(r"(\d+) прошло", line).group(1)) if "прошло" in line else 0
            bad = int(re.search(r"(\d+) упало", line).group(1)) if "упало" in line else 99
            scores[name] = (ok, bad)
        except Exception:
            scores[name] = (0, 99)
    try:
        t = subprocess.run([sys.executable, "-X", "utf8", "-m", "pytest", "evals/", "-q"],
                           cwd=str(ROOT), capture_output=True, text=True,
                           encoding="utf-8", errors="ignore", timeout=600)
        scores["тесты"] = (0 if t.returncode == 0 else 1, t.returncode)
    except Exception:
        scores["тесты"] = (0, 99)
    return scores


def _worse(before, after):
    """Стало ли хуже. Любое новое падение — да, даже если где-то стало лучше."""
    for key, (ok_b, bad_b) in before.items():
        ok_a, bad_a = after.get(key, (0, 99))
        if bad_a > bad_b or ok_a < ok_b:
            return f"{key}: было {ok_b}/{bad_b}, стало {ok_a}/{bad_a}"
    return None


def _region(text, line_no, radius=12):
    """Кусок вокруг строки: правим точку, а не файл целиком."""
    lines = text.splitlines()
    lo = max(0, line_no - 1 - radius)
    hi = min(len(lines), line_no + radius)
    return lo, hi, "\n".join(lines[lo:hi])


PROMPT = """Ты правишь один кусок кода на Python. Ничего не объясняй.

ЧТО СРАБОТАЛО: {rule}
ПОЧЕМУ ЭТО ПЛОХО: {why}

Верни ТОЛЬКО исправленный код этого куска, без пояснений, без markdown-ограды,
с теми же отступами. Правь минимально: меняй лишь то, из-за чего срабатывает
проверка. Не переименовывай, не переставляй функции, не добавляй новых
зависимостей. Если исправить нельзя без знания остального файла — верни кусок
без изменений.

КУСОК:
{code}
"""


MAX_PROMPT = 6000        # знаков: крупнее модель отвечает ошибкой сервера


def propose_patch(rule, why, code):
    """Модель переписывает кусок. Она не выбирает, что чинить, — только как.

    Задача классифицируется как «parse»: это преобразование текста по заданному
    правилу, а не выбор из вариантов. Выбор уже сделан детектором.
    """
    prompt = PROMPT.format(rule=rule, why=why, code=code)
    if len(prompt) > MAX_PROMPT:
        # Слишком большой кусок модель не берёт: отвечает ошибкой сервера, а не
        # отказом. Это выглядит как поломка улучшателя, хотя на деле упёрлись в
        # предел запроса — разница важна, чинить надо разное.
        return ""
    try:
        out = router.run("parse", prompt)
    except Exception as e:
        # Отказ модели — это отказ модели, а не приговор коду. Возвращаем пусто,
        # и правка просто не состоится: молча выдать код за исправленный нельзя.
        bus.broadcast("improver", f"Модель не ответила на правку: "
                                  f"{type(e).__name__}. Код не тронут.")
        return ""
    text = (out or "").strip()
    # Модель любит оборачивать ответ в ограду, даже когда просят не оборачивать.
    if text.startswith("```"):
        text = "\n".join(text.splitlines()[1:])
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    return text.rstrip()


def improve_one(finding):
    """Одна правка целиком: снять меру, починить, перепроверить, решить.

    Возвращает отчёт со всеми четырьмя шагами. Ни один из них не пропускается:
    правка без замера «до» ничем не доказуема.
    """
    guard.check_action("code_fix", "YELLOW")
    where = finding.get("где", "")
    rel, _, line_s = where.partition(":")
    if not rel or not line_s.isdigit():
        return {"ok": False, "why": "находка без места в коде — чинить нечего"}
    if _forbidden(rel):
        return {"ok": False, "why": f"{rel} в списке неприкосновенных: "
                                    f"судьи, правила и ключи не правятся"}
    path = ROOT / rel
    if not path.exists():
        return {"ok": False, "why": f"нет файла {rel}"}

    original = path.read_text(encoding="utf-8")
    lo, hi, region = _region(original, int(line_s))
    if hi - lo > MAX_REGION:
        return {"ok": False, "why": "кусок слишком велик для одной правки"}

    before = _audit_score()
    with telemetry.span("tool", "improver.patch", agent="improver"):
        patched_region = propose_patch(finding.get("образец", ""),
                                       finding.get("почему это плохо", ""), region)
    if not patched_region or patched_region == region:
        return {"ok": False, "why": "модель не предложила изменений"}

    lines = original.splitlines()
    new_text = "\n".join(lines[:lo] + patched_region.splitlines() + lines[hi:]) + "\n"

    # Правка, ломающая разбор файла, отвергается ДО запуска аудита: незачем
    # тратить минуты на проверку кода, который не является кодом.
    try:
        ast.parse(new_text)
    except SyntaxError as e:
        return {"ok": False, "why": f"правка ломает разбор файла: {e}"}

    BACKUP.mkdir(parents=True, exist_ok=True)
    backup = BACKUP / (path.name + ".before")
    shutil.copy2(path, backup)
    path.write_text(new_text, encoding="utf-8")

    after = _audit_score()
    worse = _worse(before, after)
    diff = "\n".join(difflib.unified_diff(region.splitlines(),
                                          patched_region.splitlines(),
                                          lineterm="", n=1))[:800]
    if worse:
        shutil.copy2(backup, path)
        bus.broadcast("improver", f"Правку {rel}:{line_s} ОТКАТИЛ: {worse}. "
                                  f"Правка выглядела разумно — этого недостаточно.")
        return {"ok": False, "откат": True, "why": worse, "diff": diff}

    _record(rel, finding, diff, before, after)
    bus.broadcast("improver", f"Правка принята: {rel}:{line_s} — {finding.get('образец')}. "
                              f"Аудит не ухудшился ни по одной мере.")
    return {"ok": True, "файл": rel, "строка": line_s, "diff": diff}


def _record(rel, finding, diff, before, after):
    from core.db import connect
    from core.regressions import now
    c = connect()
    try:
        c.execute("""INSERT INTO code_fixes(file,problem,diff,audit_before,audit_after,
                     outcome,at) VALUES (?,?,?,?,?,?,?)""",
                  (rel, finding.get("образец", "")[:200], diff[:2000],
                   str(before)[:200], str(after)[:200], "принято моделью", now()))
        c.commit()
    except Exception:
        pass
    finally:
        c.close()


def improve(limit=2):
    """Берёт находки детектора и чинит их по одной, пока не кончится запас.

    Предел намеренно мал. Пачка правок за один заход непроверяема: если после
    неё аудит просел, непонятно, какая именно навредила, и откатывать придётся
    всё. Две правки за проход — это компромисс между скоростью и тем, чтобы
    каждую можно было обсудить отдельно.
    """
    from ops import deep_checks
    # Только то, у чего есть МЕХАНИЧЕСКИ ВЕРНЫЙ ответ. Образцы с пометкой
    # НЕ_АВТО требуют знать, важен ли пойманный отказ, — это суждение, и оно
    # уходит владельцу. Модель, правящая такое, делает формально верную и по
    # существу неверную правку, а аудит её не ловит: он меряет прохождение
    # проверок, а не смысл.
    findings = [f for f in deep_checks.scan_patterns()
                if not _forbidden(f["где"].split(":")[0])
                and "НЕ_АВТО" not in f.get("образец", "")]
    if not findings:
        return "повторений известных поломок в правимых файлах нет"

    done, failed = [], []
    for f in findings[:limit]:
        r = improve_one(f)
        (done if r.get("ok") else failed).append(
            f"{f['где']} — {r.get('why') or 'принято'}")
    return (f"правок принято {len(done)}, отклонено {len(failed)}"
            + (f"; принято: {'; '.join(done)}" if done else "")
            + (f"; отклонено: {'; '.join(failed[:2])}" if failed else ""))


CYCLE = [("improve_code", lambda: improve(1))]


if __name__ == "__main__":
    print(improve(int(sys.argv[1]) if len(sys.argv) > 1 else 1))
