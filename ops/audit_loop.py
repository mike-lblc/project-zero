"""ГЛУБОКИЙ АУДИТ КАЖДЫЕ ДВА ЧАСА — с починкой там, где она доказуема.

Владелец потребовал цикл, который сам находит и латает новые дефекты. Здесь
он есть, но с одной оговоркой, которую нельзя обойти и не надо скрывать.

ПОЧЕМУ НЕ «АВТОМАТИЧЕСКИ ПЕРЕПИСЫВАТЬ ПРОБЛЕМНЫЕ УЧАСТКИ». Слепая правка кода
по срабатыванию проверки — это самый быстрый способ сломать работающую
систему. Проверка говорит «здесь образец известной поломки», а не «вот верное
исправление»: те же два слова в другом месте могут быть намеренными. Мы уже
трижды за сутки ловили детекторы, врущие про честный код, — и если бы каждый
из них правил файлы сам, мы бы получили три поломки вместо трёх ложных тревог.

ЧТО ЦИКЛ ДЕЛАЕТ ВМЕСТО:
  1. прогоняет весь набор проверок — целостность, образцы поломок, очереди,
     состояния гонки, критический путь, тесты;
  2. КЛАССЫ, ГДЕ ПОЧИНКА ДОКАЗУЕМА, отдаёт механику: он правит, перепроверяет
     аудитом и ОТКАТЫВАЕТ, если аудит после правки хуже, чем до неё;
  3. всё остальное кладёт в очередь суждений с точным местом в коде — туда,
     где владелец увидит и решит;
  4. записывает замер: сколько найдено, сколько починено, сколько осталось.
     Без этой строки «цикл работает» невозможно отличить от «цикл крутится».

ЧЕГО ЦИКЛ НЕ ДЕЛАЕТ. Не останавливает экосистему ради проверки, не правит
файлы, которые правит сам себя проверяющий код, и не объявляет проблему
решённой, если после правки аудит не стал лучше.
"""
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

REPORT = ROOT / "reports" / "audit-loop.json"

# Проверки, которые цикл прогоняет. Каждая — отдельным процессом: упавшая
# проверка не должна утаскивать за собой остальные.
SUITES = [
    ("целостность и книга", ["ops/deep_audit.py"]),
    ("образцы поломок", ["ops/deep_checks.py"]),
    ("основной аудит", ["audit.py"]),
    ("подделка работы", ["fake_work_audit.py"]),
    ("растущие инварианты", ["core/regressions.py"]),
    ("состав агента", ["agent_anatomy.py"]),
    ("заявленное подтверждено", ["verify_claims.py"]),
]


def now():
    return datetime.now(timezone.utc).isoformat()


def _run(args, timeout=900):
    try:
        r = subprocess.run([sys.executable, "-X", "utf8"] + args,
                           cwd=str(ROOT), capture_output=True, text=True,
                           encoding="utf-8", errors="ignore", timeout=timeout,
                           creationflags=0x08000000 if sys.platform == "win32" else 0)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "проверка не уложилась в отведённое время"
    except Exception as e:
        return 1, f"{type(e).__name__}: {e}"


def _tail(text, marker="ИТОГ"):
    for line in reversed((text or "").splitlines()):
        if line.startswith(marker):
            return line.strip()
    return (text or "").strip().splitlines()[-1:] and \
        (text or "").strip().splitlines()[-1][:120] or "вывод пуст"


def run_suites():
    """Прогоняет всё и возвращает итог по каждой проверке."""
    out = []
    for name, args in SUITES:
        code, text = _run(args)
        out.append({"проверка": name, "код возврата": code, "итог": _tail(text)})
    return out


def repair_what_is_provable():
    """Отдаёт механику то, что он умеет чинить доказуемо.

    Механик правит и сам себя проверяет: если аудит после правки хуже, он
    откатывает. Это единственный вид автоматической починки, который мы
    разрешаем, — у него есть мера успеха, внешняя по отношению к правке.
    """
    try:
        from agents import mechanic
        before = mechanic.find_problems()
        fixed = mechanic.repair_round() if hasattr(mechanic, "repair_round") else None
        after = mechanic.find_problems()
        return {"найдено до": len(before) if before is not None else None,
                "починено": str(fixed)[:160] if fixed else "механик не нашёл, что чинить",
                "осталось": len(after) if after is not None else None}
    except Exception as e:
        return {"ошибка": f"{type(e).__name__}: {str(e)[:120]}"}


def escalate_the_rest(findings):
    """Кладёт непочиненное в очередь суждений — с местом в коде.

    Находка без места нечинима: её нельзя ни проверить, ни исправить. Поэтому
    в эскалацию идут только записи с файлом и строкой.
    """
    from agents import council
    placed = [f for f in findings if f.get("где")]
    if not placed:
        return 0
    top = placed[:5]
    council.escalate(
        "adversary",
        f"Глубокий аудит нашёл {len(placed)} повторений известных поломок. "
        f"Автоматически их не правлю: проверка указывает на образец, а не на "
        f"верное исправление. Верхние: "
        + "; ".join(f"{f['где']} — {f['образец']}" for f in top),
        context=json.dumps(top, ensure_ascii=False))
    return len(placed)


def main():
    started = now()
    print("=" * 74)
    print(f"ГЛУБОКИЙ АУДИТ — {started[:19]}")
    print("=" * 74)

    suites = run_suites()
    for s in suites:
        mark = "ok  " if s["код возврата"] == 0 else "СБОЙ"
        print(f"  [{mark}] {s['проверка']:<26} {s['итог'][:80]}")

    from ops import deep_checks
    findings = deep_checks.scan_patterns()
    queues = deep_checks.scan_queues()
    conc = deep_checks.scan_concurrency()
    path = deep_checks.critical_path()

    print(f"\n  повторений образцов поломок: {len(findings)}")
    print(f"  очереди без второй стороны: "
          f"{sum(len(v) for v in queues.values())}")
    print(f"  замечаний по параллельной работе: {len(conc)}")
    print(f"  критический путь обрывается на: {path['обрывается на'] or 'нигде'}")

    repair = repair_what_is_provable()
    print(f"\n  механик: {repair}")

    escalated = escalate_the_rest(findings)
    print(f"  отправлено на решение владельца: {escalated}")

    report = {
        "начат": started, "закончен": now(),
        "проверки": suites,
        "повторений образцов": len(findings),
        "находки": findings[:40],
        "очереди без второй стороны": queues,
        "параллельная работа": conc,
        "критический путь": path,
        "починка механиком": repair,
        "отправлено на решение": escalated,
        "чего этот отчёт НЕ доказывает": [
            "что дефектов больше нет — проверки ищут ИЗВЕСТНЫЕ образцы",
            "что путь до денег пройден: он обрывается там, где написано выше",
            "что автоматическая починка безопасна для незнакомых классов ошибок",
        ],
    }
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    try:
        from agents.team import note
        note("adversary",
             f"DEEP AUDIT: {len(findings)} pattern repetitions, "
             f"{sum(len(v) for v in queues.values())} orphan queues, "
             f"{len(conc)} concurrency notes; critical path stops at "
             f"'{path['обрывается на'] or 'nowhere'}'.", conf=1.0)
    except Exception:
        pass

    bad = [s for s in suites if s["код возврата"] != 0]
    print("\n" + "=" * 74)
    print(f"ИТОГ: проверок упало {len(bad)} из {len(suites)}; "
          f"отчёт: {REPORT.relative_to(ROOT)}")
    print("=" * 74)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
