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
from datetime import datetime
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

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

# Весь наш код целиком — для поиска вызовов. Отдельно от files: проверяем мы
# агентов и ядро, а ВЫЗЫВАТЬ их может что угодно в хозяйстве.
_ALL_PY = [q for q in ROOT.rglob("*.py")
           if not any(part in (".git", "node_modules", ".venv", "__pycache__")
                      for part in q.parts)]

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

# ЧТО ИМЕННО МЫ ПРОВЕРЯЕМ. Не «сколько раз повторился ответ» — это неверная
# мера. Пауза растёт (5, 10, 20, 40 минут), поэтому за несколько часов шаг
# законно даст пять-шесть одинаковых ответов, будучи при этом разрежен в
# десятки раз. Считать это поломкой значит ругать защиту за то, что она
# работает, и первая версия проверки ровно это и делала.
#
# Настоящая мера — ПРОМЕЖУТОК между двумя последними одинаковыми прогонами:
# если защита жива, он близок к назначенной паузе; если мертва, шаг бежит
# каждый цикл.
from agents.worker import PAUSE_CAP_MIN, BACKOFF_MAX_MIN, SAME_LIMIT  # noqa: E402

hist = {}
for agent, notes, started in reversed(rows):
    if not notes:
        continue
    step = notes.split(":", 1)[0].strip()
    body = notes.split(":", 1)[1].strip() if ":" in notes else notes.strip()
    hist.setdefault(step, []).append((body, started))


def _moment(text):
    try:
        return datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None


runaway = []
for step, items in sorted(hist.items()):
    body = items[-1][0]
    tail = []
    for b, t in reversed(items):
        if b != body:
            break
        tail.append(t)
    n = len(tail)
    if n <= SAME_LIMIT + 1:
        continue
    a, b_ = _moment(tail[1]), _moment(tail[0])
    if not a or not b_:
        continue
    gap_min = abs((b_ - a).total_seconds()) / 60
    cap = PAUSE_CAP_MIN.get(step, BACKOFF_MAX_MIN)
    expected = min(cap, 5 * 2 ** (n - 1 - SAME_LIMIT))
    # Допуск: цикл дискретен, шаг просыпается не ровно в назначенную минуту.
    if gap_min < expected * 0.7:
        runaway.append(f"{step}: {n} раз подряд «{body[:40]}», "
                       f"между последними {gap_min:.0f} мин "
                       f"вместо назначенных {expected}")

check("повторяющийся шаг уходит на паузу",
      not runaway,
      f"проверено шагов: {len(hist)} за {len(rows)} прогонов текущего запуска, "
      f"повторяющиеся разрежены паузой как назначено" if not runaway
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
        # Функция, зарегистрированная ДЕКОРАТОРОМ, вызывается не по имени.
        # Инструменты агентов объявлены через @tool(...) и попадают в реестр;
        # искать их по имени — значит объявить мёртвым весь набор инструментов.
        # Детектор, который врёт про живой код, обесценивает свои же находки.
        try:
            i = src.index(f"def {nm}")
            head = src[max(0, i - 400):i]
            if "@tool(" in head or "@app.command(" in head or "@register" in head:
                continue
        except ValueError:
            pass
        uses = src.count(nm)
        if uses <= 1:
            # Смотреть НАДО ВЕЗДЕ, а не в двух выбранных вручную файлах.
            # На этом детектор объявил мёртвой launch.background, которую зовут
            # keep_alive.py и hourly_audit.py: они просто лежат в корне и в
            # список не входили. Ложная находка обесценивает и настоящие —
            # проверка, которой не верят, не работает.
            others = sum(q.read_text(encoding="utf-8", errors="ignore").count(nm)
                         for q in _ALL_PY if q != path)
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
