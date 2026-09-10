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
from core.db import connect
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
    c.executescript(SCHEMA)
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
    return problems


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


def repair_round(limit=3):
    """Один заход: находит проблемы и чинит самые серьёзные."""
    probs = sorted(find_problems(), key=lambda p: -p["severity"])
    if not probs:
        bus.broadcast("mechanic", "Прошёл по коду: определимых дефектов нет.")
        return "дефектов нет"
    applied = rolled = skipped = 0
    for p in probs[:limit]:
        r = apply_fix(p)
        if r["outcome"] == "applied":
            applied += 1
        elif r["outcome"] == "rolled_back":
            rolled += 1
        else:
            skipped += 1
    return (f"найдено {len(probs)}, починено {applied}, откачено {rolled}, "
            f"пропущено {skipped}")


def history(limit=15):
    c = _con()
    rows = c.execute("SELECT file,problem,outcome,detail,at FROM code_fixes "
                     "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    c.close()
    return [dict(zip(("file", "problem", "outcome", "detail", "at"), r)) for r in rows]


CYCLE = [("mechanic", lambda: repair_round(2))]


if __name__ == "__main__":
    print("═══ ЧТО МЕХАНИК НАШЁЛ В КОДЕ ═══")
    probs = sorted(find_problems(), key=lambda p: -p["severity"])
    for p in probs[:14]:
        print(f"  [{p['severity']}] {p['file']:24} {p['detail'][:62]}")
    print(f"\nвсего: {len(probs)}")
    print("\n═══ ЗАПРЕЩЁННЫЕ ФАЙЛЫ (свой надзор не трогает) ═══")
    for u in sorted(UNTOUCHABLE):
        print("  ", u)
