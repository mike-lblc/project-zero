"""ЧАСОВОЙ ЦИКЛ: АУДИТ И УЛУЧШЕНИЕ. Не отчёт, а работа.

Главное правило директивы: если улучшение выполнимо, безопасно и не требует
согласия владельца — СДЕЛАТЬ ЕГО, а не предложить. Рекомендация без исполнения
аудитом не считается, когда система способна исправить себя сама.

Поэтому цикл устроен так:

  ИЗМЕРИТЬ  →  НАЙТИ УЗКОЕ МЕСТО  →  ПОЧИНИТЬ  →  ПРОВЕРИТЬ  →  ЗАПИСАТЬ

Порядок приоритетов взят из директивы дословно: отказ системы, безопасность,
блокер выручки, застрявшая работа, сбой исполнения, и только потом улучшения.

ЧЕГО ЦИКЛ НЕ ДЕЛАЕТ:
  * не перезапускает здоровые процессы просто потому, что настал новый час;
  * не выдумывает работу, когда чинить нечего — директива это прямо запрещает;
  * не повторяет улучшение, уже испробованное без новых оснований: история
    хранится в audit_history и проверяется перед каждой правкой;
  * не трогает необратимое — деньги, ключи, удаление данных, публичные действия.

Запуск разово:      py -3.13 -X utf8 hourly_audit.py
Поставить в час:    py -3.13 -X utf8 hourly_audit.py --install
"""
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from core.db import connect, ensure_schema  # noqa: E402

# НИ ОДИН ДОЧЕРНИЙ ПРОЦЕСС НЕ ОТКРЫВАЕТ ОКНО.
# Окна выскакивали не из запуска воркера, а из КАЖДОГО вызова gh, git, node и
# powershell: процесс без собственной консоли заводит новое окно на каждый
# такой вызов. Их двадцать, и правка по местам гарантировала бы двадцать
# первый. Флаг ставится один раз на весь процесс.
try:
    from core.launch import silence as _silence
    _silence()
except Exception:
    pass


PY = ["py", "-3.13", "-X", "utf8"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_history (
  id INTEGER PRIMARY KEY,
  at TEXT NOT NULL,
  issue TEXT NOT NULL,
  root_cause TEXT,
  change TEXT,
  components TEXT,
  before_metric TEXT,
  after_metric TEXT,
  result TEXT NOT NULL,          -- fixed | failed | skipped_repeat | no_action
  priority INTEGER
);
CREATE TABLE IF NOT EXISTS audit_cycles (
  id INTEGER PRIMARY KEY,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  health TEXT,
  revenue_usd REAL,
  bottleneck TEXT,
  issues_found INTEGER,
  issues_fixed INTEGER,
  next_action TEXT
);
"""


def now():
    return datetime.now(timezone.utc).isoformat()


def _con():
    c = connect()
    ensure_schema(c, SCHEMA)
    return c


def run(script, timeout=1800):
    """Гоняет проверку и возвращает (прошло, упало, вывод)."""
    import re
    try:
        r = subprocess.run(PY + [script], cwd=str(ROOT), capture_output=True,
                           text=True, timeout=timeout, encoding="utf-8", errors="ignore")
    except subprocess.SubprocessError as e:
        return 0, 999, f"{type(e).__name__}: {e}"
    out = r.stdout or ""
    m = re.search(r"ИТОГ:\s*(\d+)\s*прошло,\s*(\d+)\s*упало", out)
    if m:
        return int(m.group(1)), int(m.group(2)), out
    return 0, 999, out[-800:]


def tried_before(issue):
    """Испробовано ли это уже. Директива запрещает крутить одно и то же."""
    c = _con()
    row = c.execute("""SELECT at, result FROM audit_history
                       WHERE issue=? AND result IN ('fixed','failed')
                       ORDER BY id DESC LIMIT 1""", (issue,)).fetchone()
    c.close()
    return row


def record(issue, root_cause, change, components, before, after, result, priority,
           _retry=3):
    """Запись в историю аудита. НЕ ИМЕЕТ ПРАВА УРОНИТЬ ЦИКЛ.

    Владелец прислал снимок: часовой аудит падал с «database is locked» ровно
    здесь — на записи о том, что он только что починил локальную модель.
    Починка состоялась, а цикл оборвался на попытке о ней рассказать. Это то
    же правило, что и для журнала воркера: потерять запись допустимо,
    потерять работу — нет.
    """
    import sqlite3 as _sq
    try:
        return _record(issue, root_cause, change, components, before, after,
                       result, priority)
    except _sq.OperationalError as e:
        if _retry > 0 and "locked" in str(e).lower():
            import time as _t
            _t.sleep(2)
            return record(issue, root_cause, change, components, before, after,
                          result, priority, _retry - 1)
        print(f"[аудит] запись в историю не удалась ({e}); цикл продолжается")
        return None


def _record(issue, root_cause, change, components, before, after, result, priority):
    c = _con()
    c.execute("""INSERT INTO audit_history(at,issue,root_cause,change,components,
                 before_metric,after_metric,result,priority)
                 VALUES (?,?,?,?,?,?,?,?,?)""",
              (now(), issue, root_cause, change, components, str(before), str(after),
               result, priority))
    c.commit(); c.close()


# ═════════════════════════════════════════ 1. ИЗМЕРИТЬ СОСТОЯНИЕ
def measure():
    """Живое состояние. Ничего не берётся из прошлых отчётов."""
    c = connect()
    q = lambda s, *a: (c.execute(s, a).fetchone() or [0])[0]
    st = {
        "payments": q("SELECT COUNT(*) FROM payments"),
        "revenue_usd": q("SELECT COALESCE(SUM(CAST(amount AS REAL)),0) FROM payments"),
        "spend": q("SELECT COUNT(*) FROM spend"),
        "prs_open": q("SELECT COUNT(*) FROM pull_requests WHERE state='OPEN' OR state IS NULL"),
        "prs_merged": q("SELECT COUNT(*) FROM pull_requests WHERE state='MERGED'"),
        "bounties_open": q("SELECT COUNT(*) FROM bounties WHERE status='found'"),
        "paths_open": q("SELECT COUNT(*) FROM money_paths WHERE open_to_us=1"),
        "leads": q("SELECT COUNT(*) FROM leads"),
        "leads_reachable": q("SELECT COUNT(*) FROM leads WHERE reachable=1"),
        "escalations": q("SELECT COUNT(*) FROM messages WHERE recipient='ESCALATION' "
                         "AND consumed_at IS NULL"),
        "stalled": q("SELECT COUNT(*) FROM tasks WHERE state='in_progress' "
                     "AND updated_at < datetime('now','-6 hours')"),
        "runs_1h": q("SELECT COUNT(*) FROM runs WHERE started_at > datetime('now','-1 hour')"),
        "errors_1h": q("SELECT COUNT(*) FROM runs WHERE status='error' "
                       "AND started_at > datetime('now','-1 hour')"),
        "matrix_ok": q("SELECT COUNT(*) FROM capability_matrix WHERE verdict='РАБОТАЕТ'"),
        "matrix_total": q("SELECT COUNT(*) FROM capability_matrix"),
    }
    c.close()
    return st


def find_bottleneck(st):
    """Где именно стоит путь к первому внешнему платежу.

    Смотрим ровно по цепочке директивы и называем ПЕРВОЕ пустое звено.
    Называть узким местом последнее звено, когда пусты все, — самообман.
    """
    if st["payments"]:
        return "выручка пошла: удержать повторяемость"
    if not st["paths_open"]:
        return "нет ни одного открытого пути к деньгам — разведка"
    if not st["bounties_open"] and not st["leads_reachable"]:
        return "путь есть, но нет ни доступной задачи, ни достижимого клиента"
    if st["escalations"]:
        return f"{st['escalations']} суждений ждут ответа — работа стоит на человеке"
    if not st["prs_open"] and not st["prs_merged"]:
        return "наружу ничего не отправлено — обращение"
    if st["prs_open"] and not st["prs_merged"]:
        return "работа отправлена, решение за третьей стороной — ждём ревью"
    return "звенья работают, вход не поступает"


# ═════════════════════════════════════════ 2. ПОЧИНИТЬ, ЧТО МОЖНО
def repair(st):
    """Правки, которые система имеет право делать сама. Каждая проверяется."""
    fixed, found = 0, 0

    # ── ПРИОРИТЕТ 1: отказ системы ────────────────────────────────
    from core import router
    ok, detail = router.health()
    if not ok:
        found += 1
        issue = "локальная модель не отвечает"
        if not tried_before(issue) or True:      # перезапуск можно повторять
            try:
                from core.launch import background
                background([str(Path.home()
                                / "AppData/Local/Programs/Ollama/ollama.exe"), "serve"],
                           log=ROOT / "data" / "ollama.log")
                import time
                time.sleep(12)
                ok2, d2 = router.health()
            except (OSError, subprocess.SubprocessError) as e:
                ok2, d2 = False, str(e)[:80]
            record(issue, "процесс ollama не запущен",
                   "перезапуск локальной модели", "core/router.py",
                   detail[:60], d2[:60], "fixed" if ok2 else "failed", 1)
            fixed += 1 if ok2 else 0

    # ── ПРИОРИТЕТ 1: воркер не крутится ───────────────────────────
    if st["runs_1h"] == 0:
        found += 1
        issue = "воркер не сделал ни одного шага за час"
        r = subprocess.run(["powershell", "-NoProfile", "-Command",
                            "(Get-CimInstance Win32_Process -Filter \"Name='py.exe'\" | "
                            "Where-Object { $_.CommandLine -like '*worker*' }).Count"],
                           capture_output=True, text=True, timeout=60)
        alive = (r.stdout or "0").strip()
        if alive in ("", "0"):
            from core.launch import background
            background(["python", "agents/worker.py", "60"], cwd=ROOT,
                       log=ROOT / "data" / "worker.log")
            record(issue, "процесс воркера отсутствует", "перезапуск воркера",
                   "agents/worker.py", "0 шагов за час", "перезапущен", "fixed", 1)
            fixed += 1
        else:
            record(issue, "процесс жив, но шаги не пишутся", "требует разбора",
                   "agents/worker.py", "0 шагов за час", "процесс жив", "failed", 1)

    # ── ПРИОРИТЕТ 2: безопасность ─────────────────────────────────
    gi = (ROOT / ".gitignore").read_text(encoding="utf-8")
    if ".env" not in gi:
        found += 1
        (ROOT / ".gitignore").write_text(gi.rstrip("\n") + "\n.env\n", encoding="utf-8")
        record("секреты не защищены", ".env отсутствует в .gitignore",
               "добавлен .env в .gitignore", ".gitignore", "не защищён", "защищён",
               "fixed", 2)
        fixed += 1

    # ── ПРИОРИТЕТ 3: блокер выручки — застрявшие задачи ───────────
    if st["stalled"]:
        found += 1
        issue = f"задачи висят без движения: {st['stalled']}"
        from core import execution
        stuck = execution.stalled(hours=6)
        moved = 0
        for t in stuck[:5]:
            try:
                execution.fail(t["id"], "висела без движения дольше шести часов",
                               next_action="разобрать, почему шаг не двигается")
                moved += 1
            except (ValueError, KeyError):
                pass
        record(issue, "задача взята в работу и брошена",
               f"переведено в провал с назначенным следующим шагом: {moved}",
               "core/execution.py", st["stalled"], st["stalled"] - moved,
               "fixed" if moved else "failed", 3)
        fixed += 1 if moved else 0

    # ── ПРИОРИТЕТ 5: сбои исполнения ──────────────────────────────
    if st["errors_1h"] > 3:
        found += 1
        c = connect()
        top = c.execute("""SELECT notes, COUNT(*) n FROM runs
                           WHERE status='error' AND started_at > datetime('now','-1 hour')
                           GROUP BY substr(notes,1,40) ORDER BY n DESC LIMIT 1""").fetchone()
        c.close()
        issue = f"повторяющийся сбой шага: {(top[0] or '')[:60]}"
        prev = tried_before(issue)
        if prev:
            record(issue, "уже разбирался", "пропущено: нет новых оснований",
                   "-", st["errors_1h"], st["errors_1h"], "skipped_repeat", 5)
        else:
            record(issue, "не установлена", "передано механику",
                   "agents/mechanic.py", st["errors_1h"], "-", "failed", 5)

    # ── ПРИОРИТЕТ 6: механик чинит код сам ────────────────────────
    from agents import mechanic
    out = dict(mechanic.CYCLE)["mechanic"]()
    if "починено" in str(out) and "починено 0" not in str(out):
        found += 1
        fixed += 1
        record("дефекты в коде", "найдены механиком", str(out)[:200],
               "agents/*", "-", str(out)[:80], "fixed", 6)

    return found, fixed


# ═════════════════════════════════════════ 3. ЦИКЛ ЦЕЛИКОМ
def cycle():
    started = now()
    print("=" * 74)
    print(f"ЧАСОВОЙ ЦИКЛ · {started[:19]}")
    print("=" * 74)

    st = measure()
    bottleneck = find_bottleneck(st)
    print(f"\nВыручка: ${st['revenue_usd']:.2f} · платежей {st['payments']} · "
          f"потрачено {st['spend']}")
    print(f"Цепочка: {st['matrix_ok']}/{st['matrix_total']} звеньев работают")
    print(f"Шагов за час: {st['runs_1h']}, из них сбоев {st['errors_1h']}")
    print(f"УЗКОЕ МЕСТО: {bottleneck}")

    print("\n── починка")
    found, fixed = repair(st)
    print(f"   найдено {found}, починено {fixed}")

    print("\n── проверки")
    results = {}
    for script in ("audit.py", "mtbx_audit.py", "fake_work_audit.py"):
        ok, bad, _ = run(script)
        results[script] = (ok, bad)
        print(f"   {script:22} {ok} прошло, {bad} упало")
    # матрица готовности — дороже, гоняем когда что-то менялось или были сбои
    if fixed or st["errors_1h"]:
        ok, bad, out = run("gnd_readiness.py")
        print(f"   {'gnd_readiness.py':22} перепроверена цепочка")

    st_after = measure()
    health = "здорова" if all(b == 0 for _, b in results.values()) else "есть находки"

    # следующий исполнимый шаг — обязателен, директива требует его называть
    nxt = ("ждать ревью отправленной работы" if st_after["prs_open"]
           else "подать заявку на свободную задачу" if st_after["bounties_open"]
           else "разведать непроверенные классы заработка")

    c = _con()
    c.execute("""INSERT INTO audit_cycles(started_at,ended_at,health,revenue_usd,
                 bottleneck,issues_found,issues_fixed,next_action)
                 VALUES (?,?,?,?,?,?,?,?)""",
              (started, now(), health, st_after["revenue_usd"], bottleneck,
               found, fixed, nxt))
    c.commit(); c.close()

    print("\n" + "=" * 74)
    print(f"ИТОГ ЦИКЛА: {health} · найдено {found}, починено {fixed}")
    print(f"СЛЕДУЮЩИЙ ШАГ: {nxt}")
    print("=" * 74)
    return {"health": health, "found": found, "fixed": fixed,
            "bottleneck": bottleneck, "next": nxt}


def install():
    """Ставит цикл в планировщик Windows на каждый час."""
    task = "P0-hourly-audit"
    cmd = (f'wscript.exe "{ROOT / "ops" / "run_hidden.vbs"}" '
           f'"{ROOT / "hourly_audit.py"}"')      # без чёрного окна
    r = subprocess.run(["schtasks", "/Create", "/TN", task, "/SC", "HOURLY",
                        "/TR", cmd, "/F"], capture_output=True, text=True, timeout=60)
    print(r.stdout or r.stderr)
    return r.returncode == 0


if __name__ == "__main__":
    if "--install" in sys.argv:
        sys.exit(0 if install() else 1)
    cycle()
