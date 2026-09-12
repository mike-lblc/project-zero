"""МАСТЕРОВОЙ — доводит баунти до слияния и следит за отправленными PR.

Что он автоматизирует из того, что в первый раз делалось руками:

  verify_claims()  — сверяет команды и флаги из документации с ИСХОДНЫМ КОДОМ
                     проекта. В первом же PR это спасло от выдуманных команд:
                     17 команд и 10 флагов проверены построчно, ни одной ошибки.
  watch_prs()      — следит за отправленными PR: ревью, замечания, конфликты,
                     провалившиеся проверки. Молчащий PR — это не «всё хорошо»,
                     это «никто не смотрел».
  find_doc_work()  — ищет баунти класса «документация и перевод»: единственный
                     класс, где качество результата проверяемо механически.

ЧЕСТНАЯ ГРАНИЦА. Написать текст документации — это суждение, а не механика,
поэтому по нашему же протоколу оно уходит на эскалацию. Мастеровой готовит
всё вокруг: находит работу, скачивает исходники, проверяет утверждения,
собирает ветку, отправляет PR и стережёт его. Сам текст пишет сильная модель.
Притворяться, что 9-миллиардная модель напишет документацию уровня слияния,
было бы враньём.
"""
import sys, re, json, time, base64, subprocess
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.db import connect, ensure_schema
from core import guard, bus

ROOT = Path(__file__).resolve().parent.parent

SCHEMA = """
CREATE TABLE IF NOT EXISTS pull_requests (
  id INTEGER PRIMARY KEY,
  url TEXT NOT NULL UNIQUE,
  repo TEXT NOT NULL,
  number INTEGER NOT NULL,
  title TEXT,
  bounty_usd REAL,
  state TEXT,
  mergeable TEXT,
  review_state TEXT,
  comments INTEGER DEFAULT 0,
  checks TEXT,
  last_checked TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS claim_checks (
  id INTEGER PRIMARY KEY,
  pr_url TEXT,
  claim TEXT NOT NULL,
  kind TEXT NOT NULL,          -- command | flag | path
  verified INTEGER NOT NULL,   -- 1 подтверждено исходниками, 0 не найдено
  source TEXT,
  checked_at TEXT NOT NULL
);
"""


def now():
    return datetime.now(timezone.utc).isoformat()


def _con():
    """Соединение со схемой ВСЕХ таблиц, которые здесь читаются.

    Мастеровой читает не только свои таблицы, но и bounties — чужую. Схему при
    этом применял только свою, и когда у находок появился новый столбец, запрос
    падал с «no such column»: таблица в базе была старой формы, потому что
    обновить её мог только тот модуль, которому она принадлежит, а он в этом
    процессе не открывался.

    Правило простое: читаешь таблицу — отвечай за её форму. Иначе порядок
    импорта модулей начинает решать, работает код или нет, а это худший вид
    зависимости: он не виден в коде и меняется сам собой.
    """
    c = connect()
    ensure_schema(c, SCHEMA)
    from agents import bounty
    ensure_schema(c, bounty.SCHEMA)
    return c


def _gh(args, timeout=45):
    try:
        r = subprocess.run(["gh"] + args, capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


def _fetch(repo, path, ref="main"):
    c = _gh(["api", f"repos/{repo}/contents/{path}?ref={ref}", "--jq", ".content"])
    if not c:
        return None
    try:
        return base64.b64decode(c).decode("utf-8", "ignore")
    except Exception:
        return None


# ---------------------------------------------------------------- 1. сверка утверждений
def verify_claims(doc_path, repo, source_paths, pr_url=None):
    """Каждая команда и флаг из документа ищется в исходниках проекта.

    Документация, где упомянута несуществующая команда, отклоняется на ревью
    и сжигает доверие. Проверка механическая, поэтому агент делает её сам.
    """
    guard.check_action("research", "GREEN")
    doc = Path(doc_path)
    if not doc.exists():
        return {"error": f"нет файла {doc_path}"}
    text = doc.read_text(encoding="utf-8")

    # Держим исходники ПО ФАЙЛАМ, а не одной кучей. Куча кажется удобнее, но в
    # ней подкоманда `list` из goal.py, memory.py и action_item.py — одно и то
    # же слово, и проверка числа аргументов путает эти команды между собой.
    by_file = {}
    for p in source_paths:
        s = _fetch(repo, p)
        if s:
            by_file[p] = s
    if not by_file:
        return {"error": "исходники не скачались — НЕ УТВЕРЖДАЕМ ничего"}
    blob = "\n".join(by_file.values())

    cmds = set()
    for m in re.finditer(r"^\s*(?:#.*)?omi\s+((?:--[a-z-]+\s+\S+\s+)*)([a-z][a-z0-9-]*)"
                         r"(?:\s+([a-z][a-z0-9-]*))?", text, re.M):
        group, sub = m.group(2), m.group(3)
        if group in ("--version", "--help"):
            continue
        cmds.add((group, sub or ""))
    # встроенные флаги Typer/Click: их нет в коде проекта, и это нормально
    BUILTIN = {"--help", "--version", "--install-completion", "--show-completion"}
    flags = set(re.findall(r"(--[a-z][a-z0-9-]{2,})", text)) - BUILTIN

    c = _con()
    ok = bad = 0
    misses = []
    for group, sub in sorted(cmds):
        found = re.search(rf'name="{re.escape(group)}"', blob) or re.search(
            rf'def {re.escape(group.replace("-", "_"))}\b', blob) or (
            f'"{group}"' in blob)
        if sub:
            found = found and (f'"{sub}"' in blob
                               or re.search(rf'def {re.escape(sub.replace("-", "_"))}\b', blob))
        claim = f"omi {group} {sub}".strip()
        c.execute("INSERT INTO claim_checks(pr_url,claim,kind,verified,source,checked_at) "
                  "VALUES (?,?,?,?,?,?)",
                  (pr_url, claim, "command", 1 if found else 0, repo, now()))
        if found:
            ok += 1
        else:
            bad += 1
            misses.append(claim)
    for f in sorted(flags):
        found = f in blob
        c.execute("INSERT INTO claim_checks(pr_url,claim,kind,verified,source,checked_at) "
                  "VALUES (?,?,?,?,?,?)", (pr_url, f, "flag", 1 if found else 0, repo, now()))
        if found:
            ok += 1
        else:
            bad += 1
            misses.append(f)

    # ЧИСЛО АРГУМЕНТОВ. Этой проверки не было, и ровно её отсутствие пропустило
    # ошибку в первый же PR: команда `omi goal progress <GOAL_ID>` существует,
    # все её слова нашлись в исходниках — а работать она не будет, потому что
    # требует ВТОРОЙ обязательный аргумент. Ревьюер нашёл это за нас.
    #
    # Существование команды и её пригодность к копированию — разные вещи.
    # Проверять надо второе: читатель копирует строку целиком.
    # Имя команды задаёт ДЕКОРАТОР, а не имя функции: @app.command("progress")
    # стоит над def update_progress. Искать по имени подкоманды — значит не найти
    # её вовсе (тихий пропуск) или найти чужую функцию (ложное обвинение). Обе
    # ошибки были в первой версии этой проверки.
    # Разбор идёт ПО ОДНОМУ ФАЙЛУ на группу команд, и имя команды берётся из
    # ДЕКОРАТОРА, а не из имени функции: @app.command("progress") стоит над
    # def update_progress. Обе эти детали были нарушены в первых версиях
    # проверки, и каждая давала уверенный неверный ответ: поиск по имени
    # функции не находил команду вовсе, а поиск по всем исходникам сразу
    # приписывал `goal list` требования от `memory list`.
    arity = {}
    for path, src in by_file.items():
        grp = Path(path).stem.replace("_", "-")            # goal.py -> goal
        for m in re.finditer(r'@\w+\.command\(\s*(?:name\s*=\s*)?"([a-z][a-z0-9-]*)"', src):
            d = src.find("\ndef ", m.end())
            if d < 0:
                continue
            # Границу списка параметров ищем ПО СКОБКАМ, а не по строке «\n) ->».
            # Однострочная сигнатура `def path(typer_ctx: typer.Context) -> None:`
            # такой строки не содержит, и поиск уезжал в следующую команду,
            # приписывая беспараметрической `config path` чужие два аргумента.
            open_i = src.find("(", d)
            if open_i < 0:
                continue
            depth, close_i = 0, -1
            for j in range(open_i, min(len(src), open_i + 4000)):
                if src[j] == "(":
                    depth += 1
                elif src[j] == ")":
                    depth -= 1
                    if depth == 0:
                        close_i = j
                        break
            if close_i < 0:
                continue
            params = src[open_i:close_i]
            arity[(grp, m.group(1))] = len(
                re.findall(r"typer\.Argument\(\s*\.\.\.", params))

    for group, sub in sorted(cmds):
        if not sub:
            continue
        required = arity.get((group, sub))
        if not required:
            continue
        # сколько аргументов написано в документе после подкоманды
        doc_calls = re.findall(rf"^\s*omi\s+(?:--\S+\s+)*{re.escape(group)}\s+"
                               rf"{re.escape(sub)}((?:\s+[^\s|#]+)*)\s*$", text, re.M)
        for tail in doc_calls:
            given = [a for a in tail.split() if not a.startswith("-")]
            claim = f"omi {group} {sub}{tail} — обязательных аргументов {required}"
            good = len(given) >= required
            c.execute("INSERT INTO claim_checks(pr_url,claim,kind,verified,source,"
                      "checked_at) VALUES (?,?,?,?,?,?)",
                      (pr_url, claim, "arity", 1 if good else 0, repo, now()))
            if good:
                ok += 1
            else:
                bad += 1
                misses.append(f"omi {group} {sub}: дано аргументов {len(given)}, "
                              f"нужно {required}")
    c.commit()
    c.close()

    if bad:
        bus.broadcast("craftsman", f"Сверка с исходниками: {ok} подтверждено, {bad} НЕ НАЙДЕНО — "
                                   f"{', '.join(misses[:5])}. Отправлять в таком виде нельзя.")
    else:
        bus.broadcast("craftsman", f"Сверка с исходниками пройдена: все {ok} утверждений "
                                   f"подтверждены кодом проекта.")
    return {"verified": ok, "missing": bad, "misses": misses}


# ---------------------------------------------------------------- 2. надзор за PR
def track_pr(url, bounty_usd=None):
    m = re.search(r"github\.com/([^/]+/[^/]+)/pull/(\d+)", url or "")
    if not m:
        return None
    repo, num = m.group(1), int(m.group(2))
    c = _con()
    c.execute("""INSERT INTO pull_requests(url,repo,number,bounty_usd,created_at)
                 VALUES (?,?,?,?,?) ON CONFLICT(url) DO NOTHING""",
              (url, repo, num, bounty_usd, now()))
    c.commit(); c.close()
    return url


def watch_prs():
    """Проверяет состояние отправленных PR. Тишина — это не «хорошо»."""
    guard.check_action("research", "GREEN")
    c = _con()
    rows = c.execute("SELECT url,repo,number,bounty_usd FROM pull_requests "
                     "WHERE state IS NULL OR state='OPEN'").fetchall()
    c.close()
    if not rows:
        return "отправленных PR нет"

    changed = []
    for url, repo, num, bounty in rows:
        out = _gh(["pr", "view", str(num), "--repo", repo, "--json",
                   "state,mergeable,reviewDecision,comments,statusCheckRollup,title",
                   "--jq", "{s:.state,m:.mergeable,r:(.reviewDecision//\"\"),"
                           "c:(.comments|length),"
                           "k:([.statusCheckRollup[]?.conclusion]|join(\",\")),"
                           "t:.title}"])
        if not out:
            continue
        try:
            d = json.loads(out)
        except Exception:
            continue
        c = _con()
        prev = c.execute("SELECT state,comments,review_state FROM pull_requests WHERE url=?",
                         (url,)).fetchone()
        c.execute("""UPDATE pull_requests SET state=?,mergeable=?,review_state=?,comments=?,
                     checks=?,title=?,last_checked=? WHERE url=?""",
                  (d["s"], d["m"], d["r"], d["c"], d["k"], d["t"], now(), url))
        c.commit(); c.close()

        if d["s"] == "MERGED":
            changed.append(f"{repo}#{num} СЛИТ" + (f" — ожидаем ${bounty:.0f}" if bounty else ""))
            bus.broadcast("craftsman",
                          f"PR {repo}#{num} СЛИТ. Если баунти подтверждён площадкой, "
                          f"выплата идёт на кошелёк. Проверка кошелька — в каждом цикле.")
        elif d["s"] == "CLOSED":
            changed.append(f"{repo}#{num} закрыт без слияния")
        elif prev and d["c"] > (prev[1] or 0):
            changed.append(f"{repo}#{num}: новых комментариев {d['c'] - (prev[1] or 0)}")
            # СОБЫТИЕ, А НЕ ЗАПИСЬ В ЖУРНАЛ. Ревью, дожидающееся своей очереди
            # сорок минут, — это отчёт задним числом, а не реакция.
            from core import events
            events.publish("pr_review_arrived",
                           {"repo": repo, "number": num, "comments": d["c"]},
                           source="craftsman")
            bus.broadcast("craftsman", f"На PR {repo}#{num} появились замечания "
                                       f"({d['c']} комментариев). Нужен ответ — молчание "
                                       f"на ревью закрывает задачу.")
        elif d["m"] == "CONFLICTING":
            changed.append(f"{repo}#{num}: КОНФЛИКТ, нужно перебазировать")
        elif d["k"] and "FAILURE" in d["k"]:
            changed.append(f"{repo}#{num}: проверки провалены ({d['k']})")

    return "; ".join(changed) if changed else f"{len(rows)} PR открыты, изменений нет"


# ---------------------------------------------------------------- 3. поиск работы по силам
def find_doc_work():
    """Баунти класса «документация и перевод»: результат проверяем механически."""
    guard.check_action("research", "GREEN")
    c = _con()
    # ТОЛЬКО ТО, ЗА ЧТО ОБЕЩАЛИ ЗАПЛАТИТЬ. Владелец сказал прямо: делать работу,
    # которая никому не нужна, — бред. Он прав, и раньше этого условия здесь не
    # было: мы написали руководство по задаче, где сумму назначил посторонний
    # соискатель, а мейнтейнеры на неё не ответили ни разу. Работа вышла
    # хорошая и никем не оплаченная.
    #
    # declared=1 означает, что награду объявил САМ ПРОЕКТ — меткой или словами
    # человека с правами в репозитории. NULL значит «не выяснили»: такие тоже
    # не берём, но по другой причине — невыясненное не то же, что отвергнутое,
    # и разведка обязана его дочистить.
    rows = c.execute("""SELECT url,repo,title,amount_usd FROM bounties
                        WHERE status='found' AND declared=1
                          AND (lower(title) LIKE '%doc%' OR lower(title) LIKE '%readme%'
                            OR lower(title) LIKE '%quickstart%' OR lower(title) LIKE '%translat%'
                            OR lower(title) LIKE '%guide%' OR lower(title) LIKE '%jsdoc%'
                            OR lower(title) LIKE '%typo%')
                        ORDER BY amount_usd DESC LIMIT 10""").fetchall()
    c.close()
    if not rows:
        return "подходящих задач по документации сейчас нет"
    bus.broadcast("craftsman", f"Задач по документации, где результат проверяем механически: "
                               f"{len(rows)}. Верхняя — {rows[0][1]} за ${rows[0][3]:.0f}.")

    # СТАВИМ ЗАДАНИЕ ИСПОЛНИТЕЛЮ. Без этой строки очередь documentation_request
    # имела читателя и не имела писателя: исполнитель ждал заданий, которые
    # некому было поставить. Способность существовала и была недостижима —
    # ровно то же, что случилось с deliver(), ждавшим темы, которую никто не
    # писал. Проверка «читают, но никто не пишет» теперь ищет такие разрывы
    # по всей системе.
    queued = _queue_for_executor(rows[0][1])
    return (f"найдено {len(rows)} задач по документации"
            + (f"; задание исполнителю поставлено (№{queued})" if queued
               else "; задание уже стоит в очереди"))


def _queue_for_executor(repo):
    """Кладёт исполнителю задание на конкретный репозиторий.

    Повторное задание на тот же репозиторий не ставится: очередь, в которую
    каждый цикл падает одно и то же, — это не работа, а счётчик оборотов.
    """
    c = _con()
    exists = c.execute(
        "SELECT id FROM messages WHERE recipient='executor' "
        "AND topic='documentation_request' AND consumed_at IS NULL "
        "AND body LIKE ?", (f'%"{repo}"%',)).fetchone()
    if exists:
        c.close()
        return None
    body = json.dumps({"repo": repo, "subdir": "", "ref": "main"}, ensure_ascii=False)
    cur = c.execute(
        "INSERT INTO messages(sender,recipient,topic,body,created_at) VALUES (?,?,?,?,?)",
        ("craftsman", "executor", "documentation_request", body, now()))
    c.commit()
    mid = cur.lastrowid
    c.close()
    return mid


# ---------------------------------------------------------------- 4. ЗАЯВКА НА ЗАДАЧУ
US = "mike-lblc"                      # наш аккаунт на GitHub, под ним всё и делается
CLAIMABLE = ("doc", "readme", "quickstart", "translat", "guide", "typo", "jsdoc",
             "comment", "docstring", "i18n", "locale")


def _log_action(kind, payload, result, dry_run):
    c = connect()
    c.execute("INSERT INTO actions(kind,action_class,dry_run,payload,result,created_at) "
              "VALUES (?,?,?,?,?,?)",
              (kind, "YELLOW", 1 if dry_run else 0, json.dumps(payload)[:900],
               str(result)[:400], now()))
    c.commit(); c.close()


def we_can_do(title):
    """Класс задачи, который мы способны закрыть механически проверяемо.

    Граница честная и узкая. Перевод и документация проверяются построчно
    сверкой с исходниками (verify_claims), поэтому за них можно браться. За
    произвольную правку кода в чужом проекте — нет: доказать её правильность
    без прогона чужих тестов мы не можем, а заявка без доказуемой работы
    ровно тот шум, за который мы отбраковываем чужие заявки.
    """
    t = (title or "").lower()
    return any(k in t for k in CLAIMABLE)


def claim(url, plan, dry_run=True):
    """Публично заявляет, что берём задачу. YELLOW: действие в чужом репозитории.

    Заявка разрешена ТОЛЬКО когда одновременно верно всё:
      * задача открыта и премия ещё не выплачена;
      * заявок меньше четырёх — иначе мы просто добавляем шум в толпу;
      * класс задачи нам по силам (we_can_do);
      * план назван конкретными файлами, а не обещанием «сделаю».

    Последнее условие важнее прочих. Мы намерили задачу, где 72 заявки и
    считанные присланные работы. Заявка без готового плана — это тот самый
    мусор; повторять его было бы лицемерием.
    """
    from agents import bounty
    m = re.search(r"github\.com/([^/]+/[^/]+)/issues/(\d+)", url or "")
    if not m:
        return {"ok": False, "why": "не похоже на ссылку на задачу"}
    repo, num = m.group(1), int(m.group(2))

    if not plan or len(plan.strip()) < 40 or not re.search(r"[\w./-]+\.\w{2,4}", plan):
        return {"ok": False, "why": "план не называет конкретных файлов — заявка была бы шумом"}

    info = _gh(["issue", "view", str(num), "--repo", repo,
                "--json", "state,title", "--jq", "{s:.state,t:.title}"])
    try:
        d = json.loads(info)
    except (json.JSONDecodeError, TypeError):
        return {"ok": False, "why": "не удалось прочитать задачу — молчание не значит «можно»"}
    if d["s"] != "OPEN":
        return {"ok": False, "why": f"задача {d['s']}, браться не за что"}
    if not we_can_do(d["t"]):
        return {"ok": False, "why": "класс задачи вне того, что мы можем доказуемо закрыть"}

    # ДВА аргумента, а не три. Здесь передавался ещё и заголовок — остаток от
    # прежней версии competition(), которая искала соперников по словам из
    # заголовка. Её переписали на подсчёт комментариев, а вызов не поправили,
    # и claim() падал с TypeError на КАЖДОМ вызове. Заявка не подавалась ни
    # разу за всё время работы системы, и это выглядело как «нет подходящих
    # задач», а не как поломка.
    rivals, paid = bounty.competition(repo, num)
    if paid:
        return {"ok": False, "why": "премия уже выплачена другому"}
    if rivals >= 4:
        return {"ok": False, "why": f"заявок уже {rivals} — идти туда значит добавлять шум"}

    body = (f"/attempt #{num}\n\n"
            f"План работы:\n{plan.strip()}\n\n"
            f"Все команды и флаги в тексте сверяются с исходниками репозитория "
            f"построчно перед отправкой; несуществующих в PR не будет.")
    guard.check_action("bounty_claim", "YELLOW")
    if dry_run:
        _log_action("bounty_claim", {"repo": repo, "issue": num}, "вхолостую", True)
        return {"ok": True, "dry_run": True, "repo": repo, "issue": num,
                "rivals": rivals, "body": body}

    out = _gh(["issue", "comment", str(num), "--repo", repo, "--body", body], timeout=60)
    ok = bool(out)
    _log_action("bounty_claim", {"repo": repo, "issue": num}, "отправлено" if ok else "отказ", False)
    if ok:
        c = _con()
        c.execute("UPDATE bounties SET status='attempted' WHERE url=?", (url,))
        c.commit(); c.close()
        bus.broadcast("craftsman", f"Заявка подана: {repo}#{num}, заявок до нас {rivals}. "
                                   f"Теперь обязаны прислать работу — заявка без работы "
                                   f"хуже, чем её отсутствие.")
    return {"ok": ok, "repo": repo, "issue": num, "rivals": rivals}


# ---------------------------------------------------------------- 5. ОТПРАВКА РАБОТЫ
def deliver(repo, branch, files, title, body, base="main", dry_run=True):
    """Форк, ветка, файлы, PR — общая способность вместо разового скрипта.

    До этого отправка существовала как ops/omi/submit.py: захардкоженные пути
    под одну конкретную задачу. Это была не способность, а один поступок.

    files — {путь_в_репозитории: текст}. Работаем через API, а не клонированием:
    репозитории бывают на гигабайты, а меняем мы два-три файла.
    """
    guard.check_action("pr_submit", "YELLOW")
    if dry_run:
        _log_action("pr_submit", {"repo": repo, "branch": branch,
                                  "files": list(files)}, "вхолостую", True)
        return {"ok": True, "dry_run": True, "files": list(files)}

    fork = f"{US}/{repo.split('/')[1]}"
    head = _gh(["api", f"repos/{repo}/git/ref/heads/{base}", "--jq", ".object.sha"])
    if not head:
        return {"ok": False, "why": f"не читается {base} в {repo}"}
    head = head.strip()

    if not _gh(["api", f"repos/{fork}"], timeout=60):
        _gh(["api", "-X", "POST", f"repos/{repo}/forks"], timeout=120)
        time.sleep(8)                       # форк создаётся не мгновенно
    _gh(["api", "-X", "POST", f"repos/{fork}/merge-upstream", "-f", f"branch={base}"])

    if not _gh(["api", f"repos/{fork}/git/ref/heads/{branch}"]):
        _gh(["api", "-X", "POST", f"repos/{fork}/git/refs",
             "-f", f"ref=refs/heads/{branch}", "-f", f"sha={head}"])

    written = []
    for path, text in files.items():
        sha = None
        cur = _gh(["api", f"repos/{fork}/contents/{path}?ref={branch}", "--jq", ".sha"])
        if cur:
            sha = cur.strip()
        payload = {"message": f"{title} ({path})", "branch": branch,
                   "content": base64.b64encode(text.encode("utf-8")).decode()}
        if sha:
            payload["sha"] = sha
        r = subprocess.run(["gh", "api", "-X", "PUT", f"repos/{fork}/contents/{path}",
                            "--input", "-"], input=json.dumps(payload),
                           capture_output=True, text=True, timeout=120)
        if r.returncode == 0:
            written.append(path)
    if not written:
        return {"ok": False, "why": "ни один файл не записался"}

    pr = _gh(["pr", "create", "--repo", repo, "--base", base,
              "--head", f"{US}:{branch}", "--title", title, "--body", body], timeout=120)
    url = (pr or "").strip().split("\n")[-1] if pr else ""
    _log_action("pr_submit", {"repo": repo, "branch": branch, "files": written},
                url or "PR не создан", False)
    if url.startswith("http"):
        track_pr(url)
        bus.broadcast("craftsman", f"Работа отправлена: {url}. Файлов {len(written)}. "
                                   f"Дальше — надзор за ревью, молчание не считаем успехом.")
        return {"ok": True, "url": url, "files": written}
    return {"ok": False, "why": "файлы записаны, но PR не создан", "files": written}


# ---------------------------------------------------------------- 6. ПОЛУЧЕНИЕ ВЫПЛАТЫ
def collect():
    """Ищет объявления о выплате НАМ и доводит их до кошелька.

    Что агент может сам: заметить, что площадка объявила выплату на наш
    аккаунт, записать ожидаемую сумму и сверить её с приходом на кошелёк.

    Чего агент НЕ может и не будет: проходить онбординг площадки. Это
    создание аккаунта и подтверждение личности — класс BLACK. Поэтому шаг
    остаётся за владельцем, и агент обязан назвать его вслух, а не тихо
    считать задачу выполненной.
    """
    guard.check_action("research", "GREEN")
    c = _con()
    rows = c.execute("SELECT url,repo,number,bounty_usd FROM pull_requests").fetchall()
    watched = [(r[1], r[2]) for r in rows]
    extra = c.execute("SELECT url,repo FROM bounties WHERE status IN ('attempted','won')").fetchall()
    c.close()
    for url, repo in extra:
        m = re.search(r"/issues/(\d+)", url or "")
        if m:
            watched.append((repo, int(m.group(1))))

    awarded = []
    for repo, num in dict.fromkeys(watched):
        out = _gh(["api", f"repos/{repo}/issues/{num}/comments?per_page=100",
                   "--jq", '[.[]|.body] | @json'], timeout=45)
        if not out:
            continue
        try:
            bodies = json.loads(out.strip().split("\n")[0])
        except (json.JSONDecodeError, IndexError):
            continue
        for b in bodies:
            low = (b or "").lower()
            if "awarded" in low and US.lower() in low:
                amt = re.search(r"\$\s?([\d,]+(?:\.\d+)?)", b)
                awarded.append({"repo": repo, "issue": num,
                                "usd": float(amt.group(1).replace(",", "")) if amt else None})
                break

    if not awarded:
        return "объявлений о выплате нам пока нет"

    from core import events
    for a in awarded:
        events.publish("payout_announced", a, source="craftsman")
    total = sum(a["usd"] or 0 for a in awarded)
    c = _con()
    for a in awarded:
        c.execute("UPDATE bounties SET status='won', note=? WHERE url LIKE ?",
                  (f"площадка объявила выплату ${a['usd'] or 0:.0f}",
                   f"%{a['repo']}/issues/{a['issue']}"))
    c.execute("""INSERT INTO human_interventions(what,why,minutes,category,
                 agent_could_have,occurred_at) VALUES (?,?,?,?,?,?)""",
              (f"забрать объявленную выплату ${total:.0f} на площадке",
               "получение требует аккаунта и подтверждения личности — класс BLACK, "
               "агентам запрещён; без этого шага деньги до кошелька не дойдут",
               10.0, "account_creation", 0, now()))
    c.commit(); c.close()
    bus.broadcast("craftsman", f"ВЫПЛАТА ОБЪЯВЛЕНА НАМ: ${total:.0f} по "
                               f"{len(awarded)} задачам. Дальше нужен владелец: "
                               f"забрать деньги можно только через аккаунт площадки, "
                               f"а это запрещённый агентам класс действий.")
    return f"объявлено выплат: {len(awarded)} на ${total:.0f} — требуется шаг владельца"


# ---------------------------------------------------------------- 7. ЦЕПОЧКА ЦЕЛИКОМ
def pursue(dry_run=True):
    """Связывает найденное с поданным: находка -> заявка -> работа -> надзор.

    Зачем это отдельно. Я построил claim() и deliver() и отчитался, что
    цепочка достроена, — а в цикл вписал только collect(). Способность, которую
    никто не вызывает, работой не является; это ровно тот же обман, что и
    заранее записанный текст, только в другом виде. Поймано детектором
    мёртвого кода, а не мной.

    Здесь цепочка замыкается. Что агент делает сам: выбирает задачу по силам,
    проверяет её на занятость и выплату, подаёт заявку с конкретным планом.
    Что уходит на эскалацию: САМ ТЕКСТ работы — это суждение, и локальной
    модели оно запрещено протоколом. Притворяться, что оно пишется само,
    было бы враньём.
    """
    from agents import bounty, council
    from core import execution
    guard.check_action("research", "GREEN")

    c = _con()
    # Здесь то же условие и по той же причине: цепочка ведёт работу к деньгам,
    # а работа без плательщика к деньгам не ведёт никуда.
    rows = c.execute("""SELECT url,repo,title,amount_usd FROM bounties
                        WHERE status='found' AND declared=1
                        ORDER BY fit_score DESC LIMIT 8""").fetchall()
    c.close()
    if not rows:
        # Разница важна: «задач нет» и «задач с подтверждённым плательщиком нет»
        # ведут к разным действиям. Первое — искать шире, второе — дочищать
        # разведку по уже найденному.
        c2 = _con()
        total = c2.execute("SELECT COUNT(*) FROM bounties WHERE status='found'").fetchone()[0]
        unknown = c2.execute("SELECT COUNT(*) FROM bounties WHERE status='found' "
                             "AND declared IS NULL").fetchone()[0]
        c2.close()
        return (f"задач с ПОДТВЕРЖДЁННЫМ плательщиком нет: найдено {total}, "
                f"из них не выяснено {unknown}; работать за «может быть» не станем")

    doable = [r for r in rows if we_can_do(r[2])]
    if not doable:
        bus.broadcast("craftsman", f"Доступных задач {len(rows)}, но ни одна не относится "
                                   f"к классу, который мы можем закрыть доказуемо. "
                                   f"Браться за остальные — обещать то, что не проверить.")
        return f"из {len(rows)} задач по силам ни одной"

    url, repo, title, usd = doable[0]
    plan = (f"Файлы: определяются по структуре {repo} перед отправкой. "
            f"Каждая команда и флаг сверяются с исходниками построчно.")
    res = claim(url, plan, dry_run=dry_run)
    if not res.get("ok"):
        return f"заявка не подана: {res.get('why')}"

    # ВХОЛОСТУЮ — НЕ ЗНАЧИТ МОЛЧА. Подача заявки в чужом репозитории необратима
    # и агенту не по уровню, поэтому цикл всегда зовёт эту цепочку вхолостую.
    # Но раньше на этом всё и заканчивалось БЕЗ СЛЕДА: критический путь обрывался
    # на «заявка подана публично», и по журналу это выглядело как отсутствие
    # подходящих задач, а не как решение, которого ждут от владельца.
    # Теперь готовая заявка уходит в очередь суждений с точным текстом — её
    # видно на дашборде, и её можно разрешить одним нажатием.
    if dry_run:
        council.escalate(
            "craftsman",
            f"Разрешить подачу заявки на {repo} (${usd or 0:.0f}). Класс задачи "
            f"проверен, занятость проверена, план назван файлами. Текст заявки "
            f"готов — нужно только ваше «да», потому что это публичное действие "
            f"в чужом репозитории.",
            context=json.dumps({"url": url, "repo": repo, "plan": plan,
                                "amount_usd": usd}, ensure_ascii=False))

    # ТЕКСТ РАБОТЫ — СУЖДЕНИЕ. Уходит по протоколу, а не пишется локально.
    council.escalate("craftsman",
                     f"Написать содержимое работы по задаче {repo} ({title[:80]}) "
                     f"на ${usd or 0:.0f}. Заявка подана, класс задачи проверен, "
                     f"занятость проверена. Нужен текст уровня слияния.",
                     context=json.dumps({"url": url, "repo": repo, "title": title,
                                         "amount_usd": usd, "dry_run": dry_run}))
    bus.broadcast("craftsman", f"Цепочка доведена до предела возможностей агента: "
                               f"{repo} за ${usd or 0:.0f}, заявка "
                               f"{'подготовлена' if dry_run else 'подана'}. Текст работы "
                               f"передан на эскалацию — писать его локальной моделью "
                               f"протокол запрещает.")
    # ДОКАЗАТЕЛЬСТВО. Без него задачу нельзя закрыть — таково правило конвейера.
    # add_proof() существовал и не вызывался ниоткуда: 22 задачи висели «в работе»
    # и только у двух было чем подтвердить работу. Правило без исполнения — не правило.
    c = _con()
    task = c.execute("SELECT id FROM tasks WHERE objective LIKE ? AND state='running' "
                     "ORDER BY id DESC LIMIT 1", (f"%{repo}%",)).fetchone()
    c.close()
    if task:
        try:
            execution.add_proof(task[0], "external_id", url,
                                f"заявка на задачу подана, класс и занятость проверены")
        except (ValueError, KeyError) as e:
            bus.broadcast("craftsman", f"Доказательство не принято конвейером: {e}")

    return (f"взята задача {repo} за ${usd or 0:.0f}; "
            f"заявка {'вхолостую' if dry_run else 'подана'}; текст на эскалации")


def fulfil(dry_run=True):
    """Забирает ответ на эскалацию и превращает его в отправленную работу.

    Это звено «выполнить обещанное». Без него цепочка обрывалась в самом
    неудачном месте: pursue() поднимал суждение на эскалацию, ответ приходил
    и ложился в таблицу сообщений — и там оставался. deliver() существовал и
    не вызывался ниоткуда, что матрица готовности и показала: НЕ ПОДКЛЮЧЕНО.

    Ответ считается работой, только если он содержит сам текст файла. Ответ
    вида «сделай хорошо» отвергается: отправить в чужой репозиторий нечего,
    а PR ради PR сжигает доверие сильнее, чем его отсутствие.

    Формат ответа: строка вида
        ФАЙЛ: путь/в/репозитории.md
        <содержимое до конца сообщения>
    """
    guard.check_action("research", "GREEN")
    c = connect()
    rows = c.execute("""SELECT id, body, created_at FROM messages
                        WHERE topic='resolution' AND consumed_at IS NULL
                        ORDER BY id""").fetchall()
    c.close()
    if not rows:
        return "разрешённых суждений, готовых к отправке, нет"

    done = 0
    for mid, body, created in rows:
        m = re.search(r"ФАЙЛ:\s*(\S+)\s*\n(.+)", body or "", re.S)
        if not m:
            bus.broadcast("craftsman", f"Ответ #{mid} не содержит файла; оставлен в очереди для исправления.")
            continue
        path, content = m.group(1), m.group(2)

        # A resolution must name its exact issue. Never guess by fit score.
        target = re.search(r"https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/issues/(\d+)", (body or "").split("ФАЙЛ:", 1)[0])
        if not target:
            bus.broadcast("craftsman", f"Ответ #{mid}: нет ссылки на исходную задачу перед ФАЙЛ; отправка отложена.")
            continue
        issue_url = target.group(0)
        c = _con()
        b = c.execute("SELECT url,repo,title,amount_usd FROM bounties WHERE url=? AND status='attempted'", (issue_url,)).fetchone()
        c.close()
        if not b:
            continue
        url, repo, title, usd = b

        branch = "docs/" + re.sub(r"[^a-z0-9-]+", "-", (title or "work").lower())[:40].strip("-")
        res = deliver(repo, branch, {path: content},
                      title=f"docs: {title[:70]}",
                      body=(f"Closes the documented gap from {url}\n\n"
                            f"Prepared by the P0 agent system. Review is requested."),
                      dry_run=dry_run)
        if res.get("ok") and not dry_run and not res.get("dry_run") and res.get("url"):
            c = connect()
            c.execute("UPDATE messages SET consumed_at=? WHERE id=?", (now(), mid))
            c.commit(); c.close()
            done += 1
        elif res.get("ok") and dry_run:
            done += 1  # prepared, not delivered; message remains pending
        else:
            bus.broadcast("craftsman", f"Отправка #{mid} не подтверждена; сообщение сохранено для повторной попытки.")
    return f"{'подготовлено' if dry_run else 'отправлено'} работ: {done} из {len(rows)}"


def status():
    c = _con()
    q = lambda s: c.execute(s).fetchone()[0]
    out = {
        "prs_open": q("SELECT COUNT(*) FROM pull_requests WHERE state='OPEN' OR state IS NULL"),
        "prs_merged": q("SELECT COUNT(*) FROM pull_requests WHERE state='MERGED'"),
        "pending_usd": q("SELECT COALESCE(SUM(bounty_usd),0) FROM pull_requests "
                         "WHERE state='OPEN' OR state IS NULL"),
        "claims_verified": q("SELECT COUNT(*) FROM claim_checks WHERE verified=1"),
        "claims_failed": q("SELECT COUNT(*) FROM claim_checks WHERE verified=0"),
    }
    c.close()
    return out


CYCLE = [("watch_prs", watch_prs), ("find_doc_work", find_doc_work),
         ("collect_payouts", collect), ("pursue", lambda: pursue(dry_run=True)),
         ("fulfil", lambda: fulfil(dry_run=True))]


if __name__ == "__main__":
    track_pr("https://github.com/BasedHardware/omi/pull/13455", 25.0)
    print("═══ СВЕРКА С ИСХОДНИКАМИ ═══")
    r = verify_claims(
        ROOT / "ops" / "omi" / "quickstart.ru.md",
        "BasedHardware/omi",
        ["sdks/python-cli/omi_cli/main.py",
         "sdks/python-cli/omi_cli/commands/auth.py",
         "sdks/python-cli/omi_cli/commands/memory.py",
         "sdks/python-cli/omi_cli/commands/conversation.py",
         "sdks/python-cli/omi_cli/commands/action_item.py",
         "sdks/python-cli/omi_cli/commands/goal.py",
         "sdks/python-cli/omi_cli/commands/local.py",
         "sdks/python-cli/omi_cli/commands/config.py"],
        pr_url="https://github.com/BasedHardware/omi/pull/13455")
    for k, v in r.items():
        print(f"  {k}: {v}")
    print("\n═══ НАДЗОР ЗА PR ═══")
    print(" ", watch_prs())
    print("\n═══ СОСТОЯНИЕ ═══")
    for k, v in status().items():
        print(f"  {k:18} {v}")
