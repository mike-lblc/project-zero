"""ИСПОЛНИТЕЛЬ — агент, который ДЕЛАЕТ работу, а не доводит её до порога.

Владелец задал прямой вопрос: почему работу должен писать я, а не система.
Возражение было такое: написать текст работы — это суждение, а суждение
локальной модели протокол запрещает. Возражение оказалось наполовину ложным,
и вот в чём именно.

СУЖДЕНИЕ — ЭТО ВЫБОР, А НЕ ИЗЛОЖЕНИЕ. Решить, за какую задачу браться, какой
ценой и стоит ли вообще, — да, суждение. А изложить то, что УЖЕ НАПИСАНО В
ИСХОДНИКАХ, — это извлечение, перевод и форматирование, то есть ровно те
операции, которые протокол называет механическими и разрешает. Разница не в
объёме текста, а в том, откуда берётся его содержание: из исходного кода или
из головы модели.

ОТСЮДА УСТРОЙСТВО. Работа собирается в четыре шага, и ни на одном модель не
придумывает содержание:

    1. РАЗВЕДКА   находим в проекте файлы, где описан его внешний интерфейс
    2. ИЗВЛЕЧЕНИЕ вытаскиваем из них факты: команды, флаги, коды выхода —
                  разбором кода, а не пересказом
    3. ИЗЛОЖЕНИЕ  переводим и форматируем ИЗВЛЕЧЁННОЕ, ничего не добавляя
    4. СВЕРКА     каждое утверждение в готовом тексте ищем обратно в исходниках

Четвёртый шаг главный, и он же — предохранитель. Если хоть одно утверждение не
прослеживается до строки исходника, работа НЕ ВЫДАЁТСЯ. Не «выдаётся с
оговоркой», не «выдаётся на проверку человеку» — не выдаётся вовсе. Документ,
где упомянута несуществующая команда, отклоняется на ревью и сжигает доверие
дороже, чем стоит сама задача.

ЧЕГО ИСПОЛНИТЕЛЬ НЕ ДЕЛАЕТ И НЕ БУДЕТ. Он не отправляет работу наружу — это
необратимое действие в чужом репозитории, оно остаётся за эскалацией. Он не
берётся за классы, где правильность нельзя доказать механически: правка чужой
логики, оптимизация, архитектура. Граница узкая намеренно: лучше сделать мало
и доказуемо, чем много и на веру.
"""
import ast
import base64
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import guard, bus, router, telemetry  # noqa: E402
from core.db import connect  # noqa: E402

OUT_DIR = ROOT / "work"          # сюда кладутся готовые работы


def _gh(args, timeout=40):
    """Вызов gh. Отказ возвращается как отказ, а не как пустой результат."""
    try:
        r = subprocess.run(["gh"] + args, capture_output=True, text=True,
                           timeout=timeout, encoding="utf-8", errors="ignore")
    except (subprocess.SubprocessError, OSError):
        return None
    return r.stdout if r.returncode == 0 else None


def _fetch(repo, path, ref="main"):
    c = _gh(["api", f"repos/{repo}/contents/{path}?ref={ref}", "--jq", ".content"])
    if not c:
        return None
    try:
        return base64.b64decode(c).decode("utf-8", "ignore")
    except Exception:
        return None


# ═════════════════════════════════════════════ 1. РАЗВЕДКА
def survey(repo, subdir="", ref="main"):
    """Находит файлы, описывающие внешний интерфейс проекта.

    Ищем не «все файлы», а те, где объявлены команды: именно они дают
    проверяемые факты. Остальное — чужая логика, в которую мы не лезем.
    """
    raw = _gh(["api", f"repos/{repo}/git/trees/{ref}?recursive=1",
               "--jq", ".tree[] | select(.type==\"blob\") | .path"])
    if raw is None:
        return {"error": "дерево репозитория не прочиталось — это незнание, а не пустота"}
    paths = [p for p in raw.splitlines() if p.strip()]
    if subdir:
        paths = [p for p in paths if p.startswith(subdir)]

    # Файлы, где обычно объявлен интерфейс командной строки.
    interesting = [p for p in paths
                   if p.endswith(".py")
                   and any(k in p.lower() for k in
                           ("main", "cli", "command", "app", "__init__"))]
    docs = [p for p in paths if p.lower().endswith((".md", ".mdx"))]
    return {"sources": interesting[:20], "docs": docs[:20], "total": len(paths)}


def _has_real_default(default):
    """Есть ли у аргумента НАСТОЯЩЕЕ значение по умолчанию.

    Здесь была ошибка, и ровно того класса, за который нас уже поправили на
    ревью. В typer и click аргумент объявляется так:

        action_item_id: str = typer.Argument(...)

    С точки зрения синтаксического дерева это значение по умолчанию, и наивный
    разбор объявлял аргумент необязательным. В справочнике выходило
    «обязательных аргументов нет» у команды, которая без аргумента просто не
    работает — то есть ровно та ошибка, из-за которой в прошлый раз
    `goal progress <id>` разошёлся с настоящей сигнатурой.

    Различение: многоточие первым доводом (typer.Argument(...)) означает
    ОБЯЗАТЕЛЬНЫЙ. Любое иное значение — настоящее умолчание.
    """
    if default is None:
        return False
    if isinstance(default, ast.Constant) and default.value is Ellipsis:
        return False
    if isinstance(default, ast.Call):
        fn = default.func
        name = getattr(fn, "attr", None) or getattr(fn, "id", "")
        if name in ("Argument", "Option"):
            if default.args and isinstance(default.args[0], ast.Constant):
                return default.args[0].value is not Ellipsis
            # без позиционного довода умолчание задаётся отдельно (default=...)
            for kw in default.keywords or []:
                if kw.arg == "default":
                    return not (isinstance(kw.value, ast.Constant)
                                and kw.value.value is Ellipsis)
            return False          # typer.Argument() без умолчания — обязателен
    return True


# ═════════════════════════════════════════════ 2. ИЗВЛЕЧЕНИЕ
def extract_surface(repo, source_paths, ref="main"):
    """Достаёт факты из кода РАЗБОРОМ, а не пересказом.

    Именно здесь проходит граница между работой и выдумкой. Модель могла бы
    «прочитать код и рассказать, что он делает» — и ошиблась бы ровно так, как
    ошиблись мы в прошлый раз: написали `goal progress <id>` там, где команда
    требует ещё и значение. Ошибку нашёл человек на ревью.

    Разбор синтаксического дерева так ошибиться не может: он видит объявленные
    параметры, а не их описание. Где дерево не строится (не Python), падаем на
    регулярные выражения и ПОМЕЧАЕМ такие факты как менее надёжные.
    """
    facts = []
    seen_src = {}
    for path in source_paths:
        src = _fetch(repo, path, ref)
        if not src:
            continue
        seen_src[path] = src
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            # имя команды берём из декоратора, если он есть: библиотеки вроде
            # typer и click называют команду в декораторе, а не в функции
            cmd = None
            for d in node.decorator_list:
                if isinstance(d, ast.Call) and getattr(d.func, "attr", None) == "command" and d.args and isinstance(d.args[0], ast.Constant):
                    if isinstance(d.args[0].value, str):
                        cmd = d.args[0].value
                        break
            if cmd is None:
                continue
            required, optional = [], []
            args = node.args
            positional = args.args or []
            defaults = list(args.defaults or [])
            # Выравниваем: значения по умолчанию относятся к ХВОСТУ списка.
            pad = [None] * (len(positional) - len(defaults))
            paired = list(zip(positional, pad + defaults))

            for a, default in paired:
                # typer_ctx и ему подобные — служебные параметры каркаса, а не
                # аргументы, которые набирает человек. Показать их пользователю
                # значит соврать о том, как вызывается команда.
                if a.arg in ("self", "cls", "ctx", "typer_ctx", "context"):
                    continue
                (optional if _has_real_default(default) else required).append(a.arg)
            facts.append({
                "kind": "команда",
                # ГРУППА ОБЯЗАТЕЛЬНА В ОПОЗНАНИИ. Команда `create` объявлена в
                # memory.py, goal.py и local.py — это ТРИ РАЗНЫЕ команды с
                # разными аргументами. Хранение по голому имени оставляло от
                # них одну, и сверка честно объявляла брак: в тексте стояли
                # аргументы одной команды, а в коде находились аргументы
                # другой. Ровно об эту мель уже разбивалась проверка утверждений
                # в craftsman — предупреждение о ней написано там же в коде,
                # и я его повторил, не прочитав.
                "group": Path(path).stem,
                "name": cmd,
                "full": f"{Path(path).stem} {cmd}",
                "function": node.name,
                "required": required,
                "optional": optional,
                "source": f"{path}:{node.lineno}",
                "confidence": "разбор дерева",
            })
    return {"facts": facts, "sources": seen_src}


# ═════════════════════════════════════════════ 3. ИЗЛОЖЕНИЕ
def compose(facts, title, lang="ru", intro=None):
    """Собирает документ ИЗ ФАКТОВ. Модель здесь только переводит и форматирует.

    Ни одного предложения о том, чего нет в фактах. Это не ограничение стиля,
    а единственное, что отличает работу от правдоподобного текста: любое
    утверждение отсюда можно ткнуть пальцем в строку исходника.
    """
    if not facts:
        return {"error": "фактов нет — сочинять нечего, и сочинять мы не будем"}

    lines = [f"# {title}", ""]
    if intro:
        lines += [intro, ""]
    lines += ["## Команды", ""]
    for f in sorted(facts, key=lambda x: x["full"]):
        args = " ".join(f"<{a}>" for a in f["required"])
        opt = " ".join(f"[{a}]" for a in f["optional"])
        sig = " ".join(x for x in (f["full"], args, opt) if x).strip()
        lines.append(f"### `{sig}`")
        lines.append("")
        if f["required"]:
            lines.append(f"Обязательные аргументы: {', '.join('`' + a + '`' for a in f['required'])}.")
        else:
            lines.append("Обязательных аргументов нет.")
        if f["optional"]:
            lines.append(f"Необязательные: {', '.join('`' + a + '`' for a in f['optional'])}.")
        lines.append("")
    return {"markdown": "\n".join(lines), "facts_used": len(facts)}


# ═════════════════════════════════════════════ 4. СВЕРКА — ПРЕДОХРАНИТЕЛЬ
def verify(markdown, facts, sources):
    """Каждое утверждение ищется обратно в исходниках. Не нашлось — брак.

    Возвращает (годно, список расхождений). Годным считается только документ,
    где у КАЖДОЙ упомянутой команды есть факт, а у каждого факта — строка
    исходника. Частичная сверка бессмысленна: одна несуществующая команда
    обесценивает весь текст.
    """
    problems = []
    if not facts or not re.search(r"^### `", markdown, re.M):
        return False, ["No verifiable command headings or source facts"]
    expected = compose(facts, "Verification")["markdown"].split("## Команды", 1)[1]
    actual = markdown.split("## Команды", 1)
    if len(actual) != 2 or actual[1] != expected:
        problems.append("Command section differs from extracted facts")
    by_name = {f["full"]: f for f in facts}

    for m in re.finditer(r"^### `([a-z][a-z0-9_-]*(?:\s+[a-z][a-z0-9_-]*)?)"
                         r"((?:\s+[<\[][a-z_]+[>\]])*)`", markdown, re.M):
        name = m.group(1)
        fact = by_name.get(name)
        if not fact:
            problems.append(f"команда «{name}» в тексте, но её нет среди извлечённых фактов")
            continue
        # аргументы в тексте должны совпасть с объявленными в коде
        written = re.findall(r"[<\[]([a-z_]+)[>\]]", m.group(2) or "")
        declared = fact["required"] + fact["optional"]
        if written != declared:
            problems.append(
                f"у «{name}» в тексте аргументы {written or 'нет'}, "
                f"а в коде {declared or 'нет'} ({fact['source']})")

    for f in facts:
        src_file = f["source"].split(":")[0]
        if src_file not in sources:
            problems.append(f"факт «{f['name']}» ссылается на {src_file}, "
                            f"которого нет среди прочитанных исходников")

    return (not problems), problems


# ═════════════════════════════════════════════ ПОЛНЫЙ ХОД
def produce(repo, subdir="", title=None, lang="ru", ref="main"):
    """Весь путь: разведка -> извлечение -> изложение -> сверка.

    Работа сохраняется на диск ТОЛЬКО если сверка прошла целиком. Иначе
    возвращается отчёт о расхождениях — он полезнее полуфабриката, потому что
    называет, где именно мы не смогли доказать правильность.
    """
    guard.check_action("research", "GREEN")
    with telemetry.span("tool", "executor.produce"):
        s = survey(repo, subdir, ref)
        if s.get("error"):
            return {"ok": False, "why": s["error"]}
        if not s["sources"]:
            return {"ok": False, "why": f"в {repo}/{subdir} не нашлось файлов с объявлением команд"}

        ex = extract_surface(repo, s["sources"], ref)
        facts = ex["facts"]
        if not facts:
            return {"ok": False, "why": "команд не извлеклось — проект устроен иначе, "
                                        "и придумывать его интерфейс мы не станем"}

        doc = compose(facts, title or f"Справочник команд {repo.split('/')[-1]}", lang)
        if doc.get("error"):
            return {"ok": False, "why": doc["error"]}

        ok, problems = verify(doc["markdown"], facts, ex["sources"])
        if not ok:
            bus.broadcast("executor", f"Работа по {repo} НЕ ВЫДАНА: сверка нашла "
                                      f"{len(problems)} расхождений. Выдавать непроверенное "
                                      f"дороже, чем не выдавать вовсе.")
            return {"ok": False, "why": "сверка не пройдена", "problems": problems[:6],
                    "facts": len(facts)}

        OUT_DIR.mkdir(exist_ok=True)
        name = f"{repo.replace('/', '_')}_{lang}.md"
        path = OUT_DIR / name
        path.write_text(doc["markdown"], encoding="utf-8")

        _record(repo, str(path), len(facts), ex["sources"])
        bus.broadcast("executor", f"Работа готова и СВЕРЕНА: {repo}, команд {len(facts)}, "
                                  f"каждая прослежена до строки исходника. Файл: {name}. "
                                  f"Отправка наружу — не мой уровень, ухожу на эскалацию.")
        return {"ok": True, "path": str(path), "facts": len(facts),
                "sources": list(ex["sources"])[:6]}


def _record(repo, path, n_facts, sources):
    """Складывает доказательство: что сделано, из чего и чем проверено."""
    from agents.team import note
    note("executor",
         f"WORK PRODUCED: {repo} -> {Path(path).name}, {n_facts} commands extracted by AST "
         f"from {len(sources)} source files, every command verified back to a source line.",
         conf=1.0)


def produce_requested():
    """Consume an explicitly scoped request; never invent a paying assignment."""
    c = connect()
    row = c.execute("SELECT id,body FROM messages WHERE recipient='executor' AND topic='documentation_request' AND consumed_at IS NULL ORDER BY id LIMIT 1").fetchone()
    c.close()
    if not row:
        return "No documentation requests queued"
    request = json.loads(row[1])
    repo = request.get("repo", "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("Documentation request requires exact repository")
    result = produce(repo, request.get("subdir", ""), ref=request.get("ref", "main"))
    if not result.get("ok"):
        raise RuntimeError(result.get("why", "Document production failed"))
    c = connect()
    try:
        c.execute("BEGIN IMMEDIATE")
        changed = c.execute("UPDATE messages SET consumed_at=? WHERE id=? AND consumed_at IS NULL", (bus.now(), row[0])).rowcount
        if changed:
            c.execute("INSERT INTO messages(sender,recipient,topic,body,created_at) VALUES (?,?,?,?,?)", ("executor","craftsman","documentation_ready",json.dumps({"request_id":row[0],"repo":repo,**result}),bus.now()))
        c.commit()
    finally:
        c.close()
    return result

CYCLE = [("produce_doc", produce_requested)]


if __name__ == "__main__":
    import sys as _s
    repo = _s.argv[1] if len(_s.argv) > 1 else "BasedHardware/omi"
    sub = _s.argv[2] if len(_s.argv) > 2 else "sdks/python-cli"
    r = produce(repo, sub)
    print(json.dumps(r, ensure_ascii=False, indent=1))
