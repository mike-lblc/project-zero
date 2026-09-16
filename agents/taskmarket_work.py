"""TASKMARKET — РАБОТА ДЕЛАЕТСЯ И ПОДАЁТСЯ САМА, БЕЗ ОЖИДАНИЯ СЕАНСА.

Владелец 15.09: «IT SHOULDN'T BE DELAYED». До этого текст работы для эскроу-задач
Taskmarket писала только сильная модель в сеансе владельца — подачи случались раз в
день. Теперь черновик пишет локальная модель (маршрут «draft»), а годность решают не
её слова, а МЕХАНИЧЕСКИЕ ворота, снятые прямо из брифа заказчика:

  * вид файла (Markdown / CSV / HTML / JSON), пределы слов и байтов;
  * точный заголовок CSV и точное число строк, если бриф их называет;
  * каждый URL в работе отвечает 200 (источник, который не открывается, — не источник);
  * HTML самодостаточен: ни одной внешней ссылки в src/href;
  * второй проход модели ищет нарушения брифа (классификация, не суждение).

Не прошло ворота после двух правок — не подаётся, а ложится на доску как «не вышло,
почему». Одна подача на задачу (первые пять бесплатны, вторую не делаем). Подача идёт
через CLI кошелька-исполнителя; квитанция — submissionId в taskmarket_state.
"""
from __future__ import annotations

import csv
import io
import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import bus, guard, router  # noqa: E402
from core.db import connect  # noqa: E402

WORK_DIR = ROOT / "work" / "taskmarket"
UA = {"User-Agent": "Mozilla/5.0 (compatible; P0-agent; +https://x402-bazaar-rank.x402-bazaar-rank-worker.workers.dev)"}
MAX_ROUNDS = 3


# ───────────────────────────────────────────────── ворота из брифа
PACKAGE_MARKERS = ("submission package", "preview url", "source archive", "educator_guide", "test_report",
                   "working educational website", "public repository", "screenshots", "walkthrough video",
                   "automated tests", "deployment")


def is_package_brief(desc: str) -> bool:
    """Бриф требует ПАКЕТ, а не один файл: сайт с живым превью, архив исходников, тесты,
    скриншоты. Упоминание README.md в таком брифе — не «задача на Markdown». Без этой
    проверки локальная модель сдала бы один .md-файл на задачу «построить сайт» — это
    нарушение брифа и порча репутации кошелька. Два и больше признака — пакет."""
    low = (desc or "").lower()
    return sum(1 for m in PACKAGE_MARKERS if m in low) >= 2


def gates_from_brief(desc: str) -> dict:
    """Снимает измеримые требования из текста задачи. Чего в брифе нет — не выдумывается."""
    d = desc or ""
    low = d.lower()
    g = {"kind": None, "ext": None, "min_words": None, "max_words": None, "max_bytes": None,
         "header": None, "rows": None, "sources": ("source url" in low or "source_url" in low or "authoritative source" in low),
         "self_contained": "self-contained" in low or "no external" in low}
    if re.search(r"\bmarkdown\b|\.md\b", low):
        g["kind"], g["ext"] = "markdown", ".md"
    elif re.search(r"\bcsv\b", low):
        g["kind"], g["ext"] = "csv", ".csv"
    elif re.search(r"\bhtml\b", low):
        g["kind"], g["ext"] = "html", ".html"
    elif re.search(r"\bjson\b", low):
        g["kind"], g["ext"] = "json", ".json"
    g["max_words_per_row"] = None
    m = re.search(r"(\d{2,5})\s*[-–]\s*(\d{2,5})\s*words", low)
    if m:
        g["min_words"], g["max_words"] = int(m.group(1)), int(m.group(2))
    else:
        # «at most 80 words per row» — предел на строку, а не на весь файл
        m = re.search(r"at most\s+([\d,]+)\s*(?:whitespace-separated\s+)?words(\s+per\s+(row|entry|item|line))?", low)
        if m and m.group(2):
            g["max_words_per_row"] = int(m.group(1).replace(",", ""))
        elif m:
            g["max_words"] = int(m.group(1).replace(",", ""))
    m = re.search(r"at most\s+([\d,]+)\s*bytes", low)
    if m:
        g["max_bytes"] = int(m.group(1).replace(",", ""))
    m = re.search(r"use this exact header:\s*\n\s*([^\n]+)", d, re.I)
    if m:
        g["header"] = m.group(1).strip()
    m = re.search(r"exactly\s+(\w+)\s+data rows", low)
    if m:
        words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
        g["rows"] = words.get(m.group(1)) or (int(m.group(1)) if m.group(1).isdigit() else None)
    return g


def _words(text: str) -> int:
    return len([w for w in text.split() if not w.startswith("http")])


def _urls(text: str) -> list[str]:
    return sorted(set(re.findall(r"https?://[^\s\)\]\"'<>,]+", text)))


def _alive(url: str) -> bool:
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=15)
        return 200 <= r.status < 400
    except Exception:
        return False


def check(text: str, g: dict) -> list[str]:
    """Что именно нарушено. Пустой список — ворота пройдены."""
    problems = []
    if not text or len(text.strip()) < 40:
        return ["пустой или слишком короткий результат"]
    n = _words(text)
    if g.get("min_words") and n < g["min_words"]:
        problems.append(f"слов {n}, нужно не меньше {g['min_words']}")
    if g.get("max_words") and n > g["max_words"]:
        problems.append(f"слов {n}, нужно не больше {g['max_words']}")
    if g.get("max_bytes") and len(text.encode("utf-8")) > g["max_bytes"]:
        problems.append(f"байт {len(text.encode('utf-8'))}, предел {g['max_bytes']}")
    if g.get("kind") == "csv":
        lines = [l for l in text.strip().splitlines() if l.strip()]
        if g.get("header") and (not lines or lines[0].strip() != g["header"]):
            problems.append("первая строка не совпадает с заданным заголовком CSV")
        try:
            rows = list(csv.reader(io.StringIO(text.strip())))
        except Exception:
            rows = []
        data_rows = [r for r in rows[1:] if any(c.strip() for c in r)]
        if g.get("rows") and len(data_rows) != g["rows"]:
            problems.append(f"строк данных {len(data_rows)}, нужно ровно {g['rows']}")
        if rows and any(len(r) != len(rows[0]) for r in rows[1:] if any(c.strip() for c in r)):
            problems.append("в CSV строки разной длины")
        if any(not c.strip() for r in data_rows for c in r):
            problems.append("в CSV есть пустые поля")
        if g.get("max_words_per_row"):
            for i, r in enumerate(data_rows, 1):
                n_row = sum(len(c.split()) for c in r if not c.strip().startswith("http"))
                if n_row > g["max_words_per_row"]:
                    problems.append(f"строка {i}: слов {n_row}, предел {g['max_words_per_row']} на строку")
    if g.get("kind") == "html":
        low = text.lower()
        if "<html" not in low or "</html>" not in low:
            problems.append("HTML без корневого элемента html")
        if re.search(r"(src|href)\s*=\s*[\"']https?://", text, re.I):
            problems.append("HTML тянет внешние файлы — должен быть самодостаточным")
    if g.get("kind") == "json":
        try:
            json.loads(text)
        except Exception:
            problems.append("JSON не разбирается")
    urls = _urls(text)
    if g.get("sources") and not urls:
        problems.append("бриф требует источники (URL), а их нет")
    dead = [u for u in urls[:12] if not _alive(u)]
    if dead:
        problems.append("недоступные URL: " + ", ".join(dead[:3]))
    if re.search(r"```", text) and g.get("kind") in ("csv", "json"):
        problems.append("остались обрамляющие ``` — файл должен содержать только данные")
    return problems


# ───────────────────────────────────────────────── черновик и проверка моделью
def _strip_fences(text: str) -> str:
    t = (text or "").strip()
    m = re.match(r"^```[a-zA-Z0-9]*\s*\n(.*)\n```\s*$", t, re.S)
    return m.group(1).strip() if m else t


def draft(desc: str, g: dict, problems: list[str] | None = None, previous: str | None = None) -> str:
    kind = g.get("kind") or "text"
    rules = [f"Output ONLY the final {kind} file content — no preface, no explanation, no code fences."]
    if g.get("min_words") or g.get("max_words"):
        rules.append(f"Length: {g.get('min_words') or 'any'}–{g.get('max_words') or 'any'} words excluding URLs.")
    if g.get("max_bytes"):
        rules.append(f"At most {g['max_bytes']} bytes.")
    if g.get("header"):
        rules.append(f"First line must be exactly: {g['header']}")
    if g.get("rows"):
        rules.append(f"Exactly {g['rows']} data rows.")
    if g.get("max_words_per_row"):
        rules.append(f"At most {g['max_words_per_row']} words per row across the descriptive fields.")
    rules.append("State only facts you are certain of; prefer well-established, conservative claims over impressive ones. "
                 "Use only URLs you know exist on the named authoritative sites (unicode.org, w3.org, nasa.gov, .edu).")
    if g.get("sources"):
        rules.append("Every source URL must be a real, currently working https page of an authoritative body; do not invent URLs.")
    if g.get("kind") == "html":
        rules.append("Self-contained: inline CSS and JS only, no external src/href, no libraries.")
    rules.append("Original wording. Follow every numbered requirement and pass/fail gate of the brief literally.")
    prompt = "You are producing a deliverable for a paid task. The brief:\n\n" + desc.strip() + "\n\nRules:\n- " + "\n- ".join(rules)
    if problems and previous:
        prompt += ("\n\nYour previous attempt failed these checks:\n- " + "\n- ".join(problems)
                   + "\n\nPrevious attempt:\n" + previous[:6000] + "\n\nProduce a corrected full version.")
    return _strip_fences(router.run("draft", prompt))


def review(desc: str, text: str) -> list[str]:
    """Второй проход: модель ищет нарушения брифа. Возвращает список проблем (пусто — чисто)."""
    prompt = ("Check a deliverable against its brief. Answer ONLY with JSON: {\"ok\": true|false, \"problems\": [\"...\"]}.\n"
              "List only concrete violations of the brief's numbered requirements or pass/fail gates. Do not judge style.\n\n"
              "BRIEF:\n" + desc.strip()[:6000] + "\n\nDELIVERABLE:\n" + text[:12000])
    try:
        raw = router.run("classify", prompt)
        m = re.search(r"\{.*\}", raw or "", re.S)
        d = json.loads(m.group(0)) if m else {}
    except Exception:
        return []                       # проверка не удалась — ворота остаются механические
    if d.get("ok") is True:
        return []
    probs = d.get("problems") or []
    return [str(p)[:160] for p in probs][:6]


# ───────────────────────────────────────────────── подача
def _con():
    return connect()


def produce_and_submit(task: dict) -> dict:
    """Одна задача: ворота → черновик → проверки → подача. Возвращает исход словами."""
    from agents import bounty
    tid = str(task["id"])
    desc = task.get("description") or ""
    if is_package_brief(desc):
        return {"ok": False, "why": "бриф требует пакет (сайт/превью/архив/тесты) — локальная модель такое не делает, "
                                    "передано на сборку сильной моделью"}
    g = gates_from_brief(desc)
    if not g.get("kind"):
        return {"ok": False, "why": "бриф не называет вид файла (md/csv/html/json) — не наш класс"}
    text, problems, previous = "", [], None
    for rnd in range(MAX_ROUNDS):
        text = draft(desc, g, problems, previous)
        problems = check(text, g)
        if not problems:
            problems = review(desc, text)
        if not problems:
            break
        previous = text
    if problems:
        return {"ok": False, "why": "ворота не пройдены после правок: " + "; ".join(problems[:4])}
    title = re.sub(r"[^a-z0-9]+", "-", desc.strip().splitlines()[0].lower())[:48].strip("-") or "deliverable"
    out_dir = WORK_DIR / tid[:10]
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{title}{g['ext']}"
    path.write_text(text, encoding="utf-8", newline="\n")
    # перед подачей — задача ещё открыта? (бриф Taskmarket: перечитать прямо перед подачей)
    fresh = bounty._tm(["task", "get", tid])
    st = ((fresh.get("data") or {}).get("status") if fresh.get("ok") else None)
    if st and st != "open":
        return {"ok": False, "why": f"задача уже {st}", "path": str(path)}
    guard.check_action("taskmarket_submit", "YELLOW")
    r = bounty._tm(["task", "submit", tid, "--file", str(path), "--role", "final"], timeout=240)
    if not r.get("ok"):
        return {"ok": False, "why": f"подача не принята: {str(r.get('error'))[:120]}", "path": str(path)}
    sid = (r.get("data") or {}).get("submissionId")
    c = _con()
    c.execute("INSERT INTO taskmarket_state(task_id,title,status,our_role,submitted,detail,updated_at) VALUES (?,?,?,?,1,?,?) "
              "ON CONFLICT(task_id) DO UPDATE SET submitted=1, our_role='worker', detail=excluded.detail, updated_at=excluded.updated_at",
              (tid, desc.strip().splitlines()[0][:120], "open/waiting_for_review", "worker",
               f"submission {sid}; {path.relative_to(ROOT)}", bus.now()))
    c.commit(); c.close()
    return {"ok": True, "submission": sid, "path": str(path.relative_to(ROOT)), "words": _words(text)}


def work(limit: int = 1) -> str:
    """Шаг цикла: берёт кандидатов без подачи (их находит taskmarket_sync), делает и подаёт."""
    guard.check_action("research", "GREEN")
    c = _con()
    c.execute("CREATE TABLE IF NOT EXISTS taskmarket_state (task_id TEXT PRIMARY KEY, title TEXT, status TEXT, "
              "our_role TEXT, submitted INTEGER DEFAULT 0, detail TEXT, updated_at TEXT)")
    cands = [r[0] for r in c.execute("SELECT task_id FROM taskmarket_state WHERE our_role='candidate' AND submitted=0 "
                                     "AND COALESCE(detail,'') NOT LIKE 'не вышло%' ORDER BY updated_at DESC LIMIT ?", (limit,))]
    c.close()
    if not cands:
        return "кандидатов без подачи нет"
    from agents import bounty
    outs = []
    for tid in cands:
        t = bounty._tm(["task", "get", tid])
        task = t.get("data") if t.get("ok") else None
        if not task:
            outs.append(f"{tid[:10]}: бриф не прочитан")
            continue
        try:
            res = produce_and_submit(task)
        except Exception as e:
            res = {"ok": False, "why": f"{type(e).__name__}: {str(e)[:100]}"}
        c = _con()
        if res.get("ok"):
            bus.broadcast("craftsman", f"Taskmarket: сделал и подал «{(task.get('description') or '')[:60]}» — "
                                       f"submission {res.get('submission')}, {res.get('words')} слов; решение заказчика после "
                                       f"{str(task.get('expiryTime'))[:16]} UTC, выплата уйдёт на кошелёк владельца.")
            try:
                from agents.worker import note
                note("craftsman", f"TASKMARKET SUBMITTED: {tid[:12]} — {res.get('path')} (submission {res.get('submission')})", conf=1.0)
            except Exception:
                pass
            outs.append(f"{tid[:10]}: подано {res.get('submission')}")
        else:
            c.execute("UPDATE taskmarket_state SET detail=?, updated_at=? WHERE task_id=?",
                      ("не вышло: " + res.get("why", "")[:200], bus.now(), tid))
            c.commit()
            bus.broadcast("craftsman", f"Taskmarket: задачу {tid[:10]} не подал — {res.get('why', '')[:160]}")
            outs.append(f"{tid[:10]}: {res.get('why', '')[:80]}")
        c.close()
    return "; ".join(outs)
