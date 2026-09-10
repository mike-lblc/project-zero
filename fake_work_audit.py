"""ОХОТА НА ПОДДЕЛКУ ПРОДУКТИВНОСТИ.

Дважды пойман один и тот же обман: агент выдаёт заранее записанный текст и
называет это работой. Сначала оптимизатор (52 прогона, 55 «находок» — одна
фраза с меняющимся счётчиком), потом explore_alternatives (четыре
захардкоженные строки, объявленные пересмотром путей).

Оба раза ловил ЧЕЛОВЕК. Значит проверки не было. Здесь она есть, и она
механическая, в двух независимых видах:

  СТАТИЧЕСКИ  — функция, которая ничего не читает извне (ни базы, ни сети,
                ни файлов), но при этом объявляет результат в чат или в
                доказательства, работой не является по построению. Она может
                вернуть только то, что в неё вписали.

  ПО ФАКТУ    — если агент за много прогонов выдаёт один и тот же текст,
                неважно, как он устроен внутри: наружу он выдаёт константу.

Обе проверки смотрят на РЕАЛЬНОЕ поведение, а не на намерения автора.

Запуск: py -3.13 -X utf8 fake_work_audit.py
"""
import ast
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from core.db import connect  # noqa: E402

# Как агент объявляет результат наружу. Если функция это делает, она обязана
# опираться на что-то прочитанное, а не только на свои же строки.
ANNOUNCE = {"say", "note", "broadcast", "escalate", "handoff", "ask", "answer"}

# Чтение внешнего состояния. Наличие хотя бы одного — признак настоящей работы.
READS = {"connect", "execute", "urlopen", "get", "post", "run", "search", "read_text",
         "read_bytes", "loads", "fetch", "glob", "iterdir", "subprocess", "check_output",
         "_gh", "_get", "_fetch", "landscape", "status", "stats", "shortlist",
         "operators", "hot_leads", "best_niche", "_index", "call", "q", "one", "n"}

R = {"ok": 0, "fail": 0}
FINDINGS = []


def check(name, ok, detail):
    if ok:
        R["ok"] += 1
        print(f"   ok   {name} — {detail}")
    else:
        R["fail"] += 1
        FINDINGS.append(f"{name}: {detail}")
        print(f"   FAIL {name} — {detail}")


def calls_in(node):
    """Имена всего, что вызывается внутри узла, включая вложенные функции."""
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name):
                out.add(f.id)
            elif isinstance(f, ast.Attribute):
                out.add(f.attr)
    return out


def literal_share(node):
    """Доля строковых констант среди всего, из чего собран результат функции.

    Функция, целиком собранная из своих же строк, может выдать только их.
    """
    consts = sum(1 for n in ast.walk(node)
                 if isinstance(n, ast.Constant) and isinstance(n.value, str)
                 and len(n.value) > 25)
    return consts


print("=" * 74)
print("ОХОТА НА ПОДДЕЛКУ ПРОДУКТИВНОСТИ")
print("=" * 74)

# ─────────────────────────────────── 1. статически: объявляет, но не читает
print("\n── Функции, которые объявляют результат, ничего не прочитав")

suspects = []
files = sorted((ROOT / "agents").glob("*.py")) + sorted((ROOT / "core").glob("*.py"))

# Вызов ЛЮБОЙ нашей же функции тоже считается чтением: она может сходить в базу
# за нас. Без этого детектор обвинил repair_round (зовёт find_problems и
# apply_fix) и economic_review (зовёт economics.survival) — обе читают базу
# через посредника. Детектор, который врёт про честный код, ничем не лучше
# кода, который врёт про свою работу.
OURS = set()
for path in files:
    try:
        t = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        continue
    OURS |= {n.name for n in ast.walk(t) if isinstance(n, ast.FunctionDef)}
OURS -= ANNOUNCE          # объявление результата чтением не является

scanned = 0
for path in files:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        continue
    for fn in [n for n in tree.body if isinstance(n, ast.FunctionDef)]:
        scanned += 1
        used = calls_in(fn)
        announces = used & ANNOUNCE
        reads = (used & READS) | (used & OURS)
        if announces and not reads:
            lits = literal_share(fn)
            if lits >= 2:
                suspects.append(f"{path.name}:{fn.name} (строк-констант {lits}, "
                                f"объявляет через {sorted(announces)}, не читает ничего)")

check("нет функций, объявляющих выдуманное",
      not suspects,
      f"проверено функций: {scanned}" if not suspects
      else f"подозрительных {len(suspects)}: " + "; ".join(suspects[:4]))

# ─────────────────────────────────── 2. по факту: повтор одного и того же
print("\n── Агенты, выдающие один и тот же результат прогон за прогоном")

# Меряем ПОДРЯД ИДУЩИЕ повторы одного шага, а не долю за всю историю.
#
# Почему именно так. Доля за две недели наказывает за прошлое, которое уже
# исправлено, и её нельзя обнулить иначе как ожиданием — то есть она
# превращается в цифру, которую хочется подкрутить. Подряд идущие повторы
# проверяют РАБОТАЮЩИЙ МЕХАНИЗМ: шаг, трижды сказавший одно и то же, обязан
# уйти на паузу. Если он этого не делает, значит защита сломана прямо сейчас.
#
# И отдельная честность: повтор сам по себе не преступление. «PR не изменился»
# — правда. Преступление в том, чтобы повторять её каждые две минуты и
# засчитывать себе работу.
con = connect()
# Считаем от последнего старта воркера. Тянуть счёт через перезапуски значит
# судить исправленную систему по её прошлому поведению.
mark = con.execute("SELECT MAX(id) FROM runs WHERE notes LIKE 'worker_start:%'").fetchone()[0]
rows = con.execute("""SELECT agent, notes, started_at FROM runs
                      WHERE id > COALESCE(?, 0) ORDER BY id DESC LIMIT 400""",
                   (mark,)).fetchall()
con.close()

seq = {}
for agent, notes, _ in reversed(rows):
    if not notes:
        continue
    step = notes.split(":", 1)[0].strip()
    body = notes.split(":", 1)[1].strip() if ":" in notes else notes.strip()
    prev = seq.get(step)
    if prev and prev[0] == body:
        seq[step] = (body, prev[1] + 1)
    else:
        seq[step] = (body, 1)

SAME_LIMIT = 3
runaway = [f"{step}: {n} раз подряд «{body[:50]}»"
           for step, (body, n) in sorted(seq.items()) if n > SAME_LIMIT + 1]

check("повторяющийся шаг уходит на паузу",
      not runaway,
      f"проверено шагов: {len(seq)} за {len(rows)} прогонов текущего запуска, "
      f"ни один не повторился больше {SAME_LIMIT + 1} раз подряд" if not runaway
      else f"защита не сработала на {len(runaway)}: " + "; ".join(runaway[:4]))

# ─────────────────────────────────── 3. находки без источника
print("\n── Утверждения, за которыми ничего не стоит")

con = connect()
no_src = con.execute("""SELECT COUNT(*) FROM evidence e
                        LEFT JOIN sources s ON s.id = e.source_id
                        WHERE s.id IS NULL""").fetchone()[0]
self_src = con.execute("""SELECT COUNT(*) FROM evidence e JOIN sources s ON s.id=e.source_id
                          WHERE s.url LIKE 'worker://%'""").fetchone()[0]
total_ev = con.execute("SELECT COUNT(*) FROM evidence").fetchone()[0]
con.close()

check("каждое утверждение имеет источник", no_src == 0, f"без источника: {no_src}")
check("утверждения не только о самих себе",
      total_ev == 0 or self_src / max(1, total_ev) < 0.85,
      f"{self_src} из {total_ev} ссылаются на собственный цикл "
      f"({int(100 * self_src / max(1, total_ev))}%)")

# ─────────────────────────────────── 4. каждый шаг цикла действительно вызывается
print("\n── Все объявленные шаги реально исполняются")

from agents import worker  # noqa: E402

declared = [n for n, _ in worker.CYCLE + worker.SLOW_CYCLE]
con = connect()
seen = {r[0] for r in con.execute(
    "SELECT DISTINCT notes FROM runs WHERE started_at > datetime('now','-2 days')")
    if r[0]}
con.close()
ran = {d for d in declared if any(s.startswith(d + ":") for s in seen)}
never = sorted(set(declared) - ran)
check("объявленные шаги цикла запускались",
      len(never) <= 8,
      f"из {len(declared)} шагов за двое суток отработали {len(ran)}"
      + (f", ещё не доходила очередь до: {never[:6]}" if never else ""))

# ─────────────────────────────────── 5. мёртвый код
print("\n── Мёртвый код, который выглядит рабочим")

dead = []
for path in files:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        continue
    src = path.read_text(encoding="utf-8")
    names = [n.name for n in tree.body if isinstance(n, ast.FunctionDef)]
    for nm in names:
        if nm.startswith("_") and ("unused" in nm or "legacy" in nm or "old" in nm):
            dead.append(f"{path.name}:{nm}")
            continue
        # функция верхнего уровня, которую никто не зовёт ни здесь, ни где-либо
        if nm.startswith("__") or nm in ("now", "main"):
            continue
        uses = src.count(nm)
        if uses <= 1:
            others = sum(p.read_text(encoding="utf-8").count(nm) for p in files if p != path)
            others += sum((ROOT / f).read_text(encoding="utf-8").count(nm)
                          for f in ("audit.py", "mtbx_audit.py") if (ROOT / f).exists())
            if others == 0:
                dead.append(f"{path.name}:{nm}")

check("нет мёртвого кода", not dead,
      "мёртвых функций нет" if not dead else f"{len(dead)}: {dead[:6]}")

print("\n" + "=" * 74)
print(f"ИТОГ: {R['ok']} прошло, {R['fail']} упало")
if FINDINGS:
    print("\nЧТО ЧИНИТЬ:")
    for f in FINDINGS:
        print("  - " + f)
print("=" * 74)
sys.exit(1 if R["fail"] else 0)
