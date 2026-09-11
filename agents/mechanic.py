"""МЕХАНИК — единственный агент, которому разрешено менять КОД.

Почему раньше было запрещено: агент, правящий код без проверки, тихо ломает
систему. Почему теперь можно: у нас есть готовый гейт — аудит на 89 проверок.

Правило простое и жёсткое:

    правка -> полный аудит -> если хоть одна проверка упала, ОТКАТ

Механик не «старается не сломать». Он ломает и откатывается, и это фиксируется.

ЧЕГО ОН НЕ ТРОГАЕТ НИКОГДА (иначе он отключит собственный надзор):
    audit.py          — его же гейт
    core/guard.py     — запреты действий
    core/execution.py — правила исполнения
    .env, .gitignore  — секреты и защита репозитория

Каждая правка сохраняется целиком до и после, поэтому откат возможен всегда,
даже если git недоступен.
"""
import sys, re, ast, json, shutil, subprocess, difflib
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.db import connect, ensure_schema
from core import guard, bus

ROOT = Path(__file__).resolve().parent.parent
BACKUP = ROOT / "data" / "code_backups"

# Файлы, которые механику запрещено менять: это его собственный надзор.
UNTOUCHABLE = {"audit.py", "core/guard.py", "core/execution.py", "core/db.py",
               ".env", ".gitignore", "agents/mechanic.py"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS code_fixes (
  id INTEGER PRIMARY KEY,
  file TEXT NOT NULL,
  problem TEXT NOT NULL,
  diff TEXT,
  audit_before INTEGER,
  audit_after INTEGER,
  outcome TEXT NOT NULL,        -- applied | rolled_back | skipped
  detail TEXT,
  at TEXT NOT NULL
);
"""


def now():
    return datetime.now(timezone.utc).isoformat()


def _con():
    c = connect()
    ensure_schema(c, SCHEMA)
    return c


def _rel(p):
    return str(Path(p).relative_to(ROOT)).replace("\\", "/")


# ---------------------------------------------------------------- аудит как гейт
def run_audit():
    """Возвращает (прошло, упало). Это единственный судья правок."""
    try:
        r = subprocess.run(["py", "-3.13", "-X", "utf8", "audit.py"],
                           cwd=str(ROOT), capture_output=True, text=True, timeout=600)
        out = r.stdout or ""
        m = re.search(r"ИТОГ:\s*(\d+)\s*прошло,\s*(\d+)\s*упало", out)
        if m:
            return int(m.group(1)), int(m.group(2))
        return 0, 999
    except Exception:
        return 0, 999


# ---------------------------------------------------------------- поиск проблем
def find_problems():
    """Ищет ТОЧНО ОПРЕДЕЛИМЫЕ дефекты. Никаких «мне кажется, тут некрасиво»."""
    guard.check_action("research", "GREEN")
    problems = []
    for f in list((ROOT / "agents").glob("*.py")) + list((ROOT / "core").glob("*.py")):
        rel = _rel(f)
        if rel in UNTOUCHABLE:
            continue
        src = f.read_text(encoding="utf-8")
        try:
            tree = ast.parse(src)
        except SyntaxError as e:
            problems.append({"file": rel, "kind": "syntax_error",
                             "detail": f"файл не парсится: {e}", "severity": 5})
            continue

        # голый except: глотает всё, включая KeyboardInterrupt и ошибки в наших же гейтах
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and node.type is None:
                problems.append({"file": rel, "kind": "bare_except",
                                 "detail": f"голый except в строке {node.lineno} — "
                                           f"глотает любые ошибки, включая наши гейты",
                                 "line": node.lineno, "severity": 3})

        # неиспользованные импорты
        imported, used = {}, set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    imported[(a.asname or a.name).split(".")[0]] = node.lineno
            elif isinstance(node, ast.ImportFrom):
                for a in node.names:
                    imported[a.asname or a.name] = node.lineno
            elif isinstance(node, ast.Name):
                used.add(node.id)
            elif isinstance(node, ast.Attribute):
                n = node
                while isinstance(n, ast.Attribute):
                    n = n.value
                if isinstance(n, ast.Name):
                    used.add(n.id)
        for name, line in imported.items():
            if name not in used and name not in ("annotations",):
                problems.append({"file": rel, "kind": "unused_import",
                                 "detail": f"импорт '{name}' в строке {line} не используется",
                                 "line": line, "target": name, "severity": 1})

        # ── ПАРАМЕТР ПРИНЯТ И ВЫБРОШЕН ────────────────────────────────
        # Владелец спросил, почему чинящий агент не поймал поломку. Честный
        # ответ: не мог — он умел находить только голый except и лишний импорт.
        # Настоящий дефект выглядел так: _leads(fn_name) принимал имя шага и
        # НИКОГДА его не использовал, всегда вызывая одно и то же. Шаги,
        # вписанные в цикл, не запускались, а журнал показывал бодрый результат.
        # Такую поломку не видно ни по падениям, ни по тестам — только по тому,
        # что объявленный параметр нигде не встречается в теле.
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            args = [a.arg for a in node.args.args + node.args.kwonlyargs
                    if a.arg not in ("self", "cls", "_")]
            if not args:
                continue
            # Смотрим ТОЛЬКО на тело. Дамп всей функции содержит объявление
            # самого параметра (arg='fn_name'), и проверка находила его же,
            # успокаивалась и пропускала дефект — то есть детектор ловил
            # собственный хвост. На этом он и провалил первую же проверку
            # против настоящей поломки.
            body_names = set()
            for stmt in node.body:
                body_names |= {n.id for n in ast.walk(stmt) if isinstance(n, ast.Name)}
                body_names |= {n.attr for n in ast.walk(stmt) if isinstance(n, ast.Attribute)}
            # f-строки и обращения по ключу тоже считаются использованием
            body_text = " ".join(ast.dump(s) for s in node.body)
            for a in args:
                if a in body_names or f"'{a}'" in body_text:
                    continue
                problems.append({
                    "file": rel, "kind": "ignored_parameter",
                    "detail": f"функция '{node.name}' (строка {node.lineno}) принимает "
                              f"'{a}' и никогда его не использует — вызывающий думает, "
                              f"что управляет поведением, а оно не меняется",
                    "line": node.lineno, "target": a, "severity": 4})

        # ── МЕСТНОЕ ВРЕМЯ, ОБЪЯВЛЕННОЕ ВСЕМИРНЫМ ──────────────────────
        # Дописать '+00:00' к наивной метке — значит объявить местное время UTC.
        # Данные выглядят исправными и уезжают на разницу поясов; у нас так
        # уехали десять записей журнала, и проверка живости показала будущее.
        for i, line in enumerate(src.splitlines(), 1):
            if re.search(r'\+\s*["\']\+00:00["\']', line):
                problems.append({
                    "file": rel, "kind": "naive_time_as_utc",
                    "detail": f"строка {i}: к метке времени дописывается '+00:00' — "
                              f"если метка местная, это объявляет её всемирной и "
                              f"сдвигает данные на разницу поясов",
                    "line": i, "severity": 4})

        # ── УДАЛЕНИЕ БЕЗ УСЛОВИЯ ──────────────────────────────────────
        # DELETE по всей таблице берёт исключительную блокировку. У нас такой
        # вызов в уборке повторов ронял шаг с «database is locked» даже когда
        # удалять было нечего.
        for i, line in enumerate(src.splitlines(), 1):
            if re.search(r"DELETE\s+FROM\s+\w+\s*(?:\"\"\"|'''|\"|')?\s*$", line, re.I):
                problems.append({
                    "file": rel, "kind": "unguarded_delete",
                    "detail": f"строка {i}: DELETE по всей таблице без условия — "
                              f"берёт исключительную блокировку и роняет "
                              f"параллельных писателей",
                    "line": i, "severity": 3})

    # ── ССЫЛКА НА ОТСУТСТВУЮЩИЙ АТРИБУТ МОДУЛЯ ────────────────────────
    # Самый тихий класс: код ссылается на module.ATTR, которого в модуле нет.
    # Так исчез mechanic.CYCLE — его снесли вместе с соседней функцией, и шаг
    # механика падал при каждом вызове. Ни синтаксис, ни импорты этого не видят.
    problems += _missing_attributes()
    return problems


def _missing_attributes():
    """Ищет обращения к module.ATTR, которых в модуле не существует."""
    out = []
    files = list((ROOT / "agents").glob("*.py")) + list((ROOT / "core").glob("*.py"))
    # что каждый модуль объявляет на верхнем уровне
    declared = {}
    for f in files:
        try:
            t = ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        names = set()
        for n in t.body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(n.name)
            elif isinstance(n, ast.Assign):
                names |= {x.id for x in n.targets if isinstance(x, ast.Name)}
            elif isinstance(n, (ast.Import, ast.ImportFrom)):
                names |= {(a.asname or a.name).split(".")[0] for a in n.names}
        declared[f.stem] = names

    for f in files:
        rel = _rel(f)
        try:
            t = ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for n in ast.walk(t):
            if not (isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)):
                continue
            mod, attr = n.value.id, n.attr
            if mod not in declared or mod == f.stem:
                continue
            if attr.startswith("__") or attr in declared[mod]:
                continue
            out.append({
                "file": rel, "kind": "missing_attribute",
                "detail": f"строка {n.lineno}: обращение к {mod}.{attr}, "
                          f"но в модуле {mod} такого имени нет — вызов упадёт",
                "line": n.lineno, "severity": 5})
    return out


# ---------------------------------------------------------------- правка
def _backup(path):
    BACKUP.mkdir(parents=True, exist_ok=True)
    dst = BACKUP / (Path(path).name + "." + datetime.now().strftime("%H%M%S") + ".bak")
    shutil.copy2(path, dst)
    return dst


def apply_fix(problem, dry_run=False):
    """Чинит одну проблему и проверяет аудитом. Не прошло — откат."""
    rel = problem["file"]
    if rel in UNTOUCHABLE:
        return {"outcome": "skipped", "detail": "файл под запретом"}
    path = ROOT / rel
    original = path.read_text(encoding="utf-8")
    lines = original.splitlines(keepends=True)
    kind = problem["kind"]
    new = None

    if kind == "unused_import":
        i = problem["line"] - 1
        if 0 <= i < len(lines):
            ln = lines[i]
            name = problem["target"]
            # строка вида "import x" или "from y import a, b" — убираем аккуратно
            if re.match(rf"^\s*import\s+{re.escape(name)}\s*$", ln.rstrip("\n")):
                new = "".join(lines[:i] + lines[i + 1:])
            elif "," in ln and re.search(rf"\b{re.escape(name)}\b", ln):
                fixed = re.sub(rf",\s*{re.escape(name)}\b", "", ln)
                fixed = re.sub(rf"\b{re.escape(name)}\s*,\s*", "", fixed)
                if fixed != ln:
                    new = "".join(lines[:i] + [fixed] + lines[i + 1:])

    elif kind == "bare_except":
        i = problem["line"] - 1
        if 0 <= i < len(lines) and re.match(r"^\s*except\s*:", lines[i]):
            fixed = re.sub(r"except\s*:", "except Exception:", lines[i])
            new = "".join(lines[:i] + [fixed] + lines[i + 1:])

    if new is None or new == original:
        return {"outcome": "skipped", "detail": "не нашёл безопасной правки"}

    diff = "".join(difflib.unified_diff(original.splitlines(True), new.splitlines(True),
                                        fromfile=rel, tofile=rel + " (после)", n=1))[:1500]
    if dry_run:
        return {"outcome": "skipped", "detail": "проверка вхолостую", "diff": diff}

    before_ok, before_fail = run_audit()
    bak = _backup(path)
    path.write_text(new, encoding="utf-8")
    after_ok, after_fail = run_audit()

    c = _con()
    if after_fail > before_fail or after_ok < before_ok:
        shutil.copy2(bak, path)                       # ОТКАТ
        outcome, detail = "rolled_back", (f"аудит ухудшился: было {before_ok}/{before_fail}, "
                                          f"стало {after_ok}/{after_fail}. Файл возвращён.")
        bus.broadcast("mechanic", f"Правка в {rel} ОТКАЧЕНА: {detail}")
    else:
        outcome, detail = "applied", f"аудит: {before_ok}/{before_fail} -> {after_ok}/{after_fail}"
        bus.broadcast("mechanic", f"Починил {rel}: {problem['detail']}. {detail}")
    c.execute("""INSERT INTO code_fixes(file,problem,diff,audit_before,audit_after,outcome,detail,at)
                 VALUES (?,?,?,?,?,?,?,?)""",
              (rel, problem["detail"], diff, before_ok, after_ok, outcome, detail, now()))
    c.commit(); c.close()
    return {"outcome": outcome, "detail": detail, "diff": diff}


def publish(files):
    """Отправляет применённые правки в репозиторий.

    Без этого шага починки живут только на этой машине, а в облаке
    (GitHub Actions) продолжает крутиться старый код. Коммитим ИМЕННО
    те файлы, которые механик изменил, — ничего лишнего под руку не
    попадает. Ошибка git не срывает заход: работа уже сделана на диске.
    """
    files = [f for f in dict.fromkeys(files) if f not in UNTOUCHABLE]
    if not files:
        return "публиковать нечего"
    try:
        subprocess.run(["git", "add", "--"] + files, cwd=str(ROOT),
                       capture_output=True, text=True, timeout=60, check=True)
        msg = ("fix(mechanic): " + ", ".join(files[:3])
               + (f" и ещё {len(files) - 3}" if len(files) > 3 else ""))
        r = subprocess.run(["git", "commit", "-m", msg], cwd=str(ROOT),
                           capture_output=True, text=True, timeout=60)
        if r.returncode != 0 and "nothing to commit" not in (r.stdout or ""):
            return f"коммит не прошёл: {(r.stderr or r.stdout).strip()[:120]}"
        p = subprocess.run(["git", "push"], cwd=str(ROOT),
                           capture_output=True, text=True, timeout=180)
        if p.returncode != 0:
            return f"запушить не удалось: {(p.stderr or '').strip()[:120]}"
        bus.broadcast("mechanic", f"Правки отправлены в репозиторий: {', '.join(files)}. "
                                  f"Теперь и облачный прогон работает с исправленным кодом.")
        return f"опубликовано файлов: {len(files)}"
    except (subprocess.SubprocessError, OSError) as e:
        return f"git недоступен: {type(e).__name__}"


def repair_round(limit=3):
    """Один заход: находит проблемы, чинит самые серьёзные и публикует их."""
    probs = sorted(find_problems(), key=lambda p: -p["severity"])
    if not probs:
        bus.broadcast("mechanic", "Прошёл по коду: определимых дефектов нет.")
        return "дефектов нет"
    applied = rolled = skipped = 0
    fixed_files = []
    for p in probs[:limit]:
        r = apply_fix(p)
        if r["outcome"] == "applied":
            applied += 1
            fixed_files.append(p["file"])          # уже относительный путь
        elif r["outcome"] == "rolled_back":
            rolled += 1
        else:
            skipped += 1
    tail = f"; {publish(fixed_files)}" if applied else ""
    return (f"найдено {len(probs)}, починено {applied}, откачено {rolled}, "
            f"пропущено {skipped}{tail}")




CYCLE = [("mechanic", lambda: repair_round(2))]


if __name__ == "__main__":
    print("═══ ПОИСК ДЕФЕКТОВ ═══")
    for p in sorted(find_problems(), key=lambda x: -x["severity"])[:12]:
        print(f"  [{p['severity']}] {p['file']}: {p['detail']}")
    print()
    print("═══ ЗАХОД ПОЧИНКИ ═══")
    print(" ", repair_round(2))
