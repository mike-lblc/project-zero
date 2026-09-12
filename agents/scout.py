"""SCOUT — the first agent. Searches, fetches, extracts, and files evidence.
Every claim it makes MUST carry a source row, per DECISION_PROTOCOL.md.
Scout is a GREEN agent: research only, reversible, private, free."""
import sys, re, json, hashlib, urllib.request, urllib.parse, urllib.error
from pathlib import Path
from datetime import datetime, timezone
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.db import connect
from core import router, guard, memory

UA = "Mozilla/5.0 (compatible; P0-scout/0.1)"
NAME = "scout"

def now(): return datetime.now(timezone.utc).isoformat()

def _get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA,
          "Accept": "text/html,application/xhtml+xml"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")

# ПОИСКОВИКИ. Проверено живыми запросами 2026-09-13.
#
# Прежний порядок был brave → bing → duckduckgo, и поиск тихо умер, не бросив
# ни одной ошибки. Brave отвечал 429, DuckDuckGo рвал TLS на html-версии, а
# Bing отдавал 120 КБ разметки — и цикл на этом останавливался, потому что
# условием выхода была ДЛИНА страницы, а не найденные результаты. Шаблон Bing
# при этом не совпадал ни разу (`<h2>` стал `<h2 class="">`), и наружу уходил
# пустой список. Проспектор принимал его за «в этом классе ничего нет» и
# крутил один и тот же класс сутками.
#
# Bing из списка УБРАН, а не починен: на запрос «prediction market api rewards
# accuracy» он отдаёт десять страниц про актрису Кэтлин Тёрнер. Это подменённая
# выдача для роботов. Починив разбор, мы бы начали записывать чешские
# киносайты в площадки заработка.
#
# Маскироваться под браузер, чтобы обойти такую защиту, мы НЕ будем. Это
# обход проверки, отличающей человека от машины, и он запрещён нашими же
# правилами. Поэтому здесь только те, кто отвечает честному роботу по делу:
#   duckduckgo lite  — выдача по запросу, ссылки завёрнуты в /l/?uddg=
#   marginalia       — открытый API для машин, ключ «public» объявлен ими самими
#   brave            — работает, но быстро упирается в 429
# Mojeek проверен и отвечает капчей — капчу мы не решаем, он не подключён.
ENGINES = (
    ("ddg-lite", "https://lite.duckduckgo.com/lite/?q={q}", "html"),
    ("marginalia", "https://api.marginalia.nu/public/search/{q}?count=20", "json"),
    ("brave", "https://search.brave.com/search?q={q}", "html"),
)
_PATTERNS = {
    "ddg-lite": (r"<a[^>]+href=\"([^\"]+)\"[^>]+class=.result-link.[^>]*>(.*?)</a>",
                 r"<a[^>]+class=.result-link.[^>]+href=\"([^\"]+)\"[^>]*>(.*?)</a>"),
    "brave": (r'<a[^>]+href="(https?://[^"]+)"[^>]*>\s*<div[^>]*>(.*?)</div>',),
}
# Сколько секунд не трогать движок, ответивший 429. В памяти процесса — это
# только экономия запросов: забытая пауза стоит один лишний отказ, а не
# неверный вывод.
_COOLDOWN = {}
COOLDOWN_SEC = 900
# Не чаще одного обращения к движку за столько секунд. DuckDuckGo после пяти
# запросов подряд показал проверку «выберите квадраты с утками». Слишком
# частый движок пропускается в пользу следующего; а если заняты ВСЕ, поиск
# ждёт ближайший — не дольше этого интервала. Сдаться здесь значило бы снова
# выдать нашу торопливость за поломку источника.
MIN_GAP_SEC = 20
_LAST = {}
# Признаки проверки «человек ли вы». Встретив их, мы уходим, а не решаем.
_HUMAN_CHECK = re.compile(r"(?i)captcha|confirm this search was made by a human|"
                          r"are you a robot|unusual traffic")
_STOP = {"the", "and", "for", "with", "from", "that", "this", "your", "you",
         "paid", "pays", "free", "open", "remote", "service", "platform"}


def _unwrap(href):
    """Настоящий адрес из обёртки поисковика."""
    import html as _html
    href = _html.unescape(href)
    if href.startswith("//"):
        href = "https:" + href
    m = re.search(r"[?&]uddg=([^&]+)", href)
    return urllib.parse.unquote(m.group(1)) if m else href


def _relevant(query, url, title):
    """Есть ли в результате хоть одно значимое слово запроса.

    Проверка грубая намеренно: она не оценивает качество, она ловит подмену —
    выдачу, которая к запросу не относится вовсе.
    """
    words = [w for w in re.findall(r"[a-z0-9]{4,}", query.lower()) if w not in _STOP]
    hay = (url + " " + title).lower()
    return not words or any(w in hay for w in words)


def _parse(name, kind, body):
    if kind == "json":
        return [(r.get("url", ""), r.get("title", "")) for r in json.loads(body).get("results", [])]
    for pat in _PATTERNS[name]:
        found = re.findall(pat, body, re.S)
        if found:
            return found
    return []


def search(query, limit=8):
    """Бесплатный поиск без ключа. ТРИ ИСХОДА, И ОНИ НЕ СМЕШИВАЮТСЯ:

      список результатов          — поиск прошёл, результаты относятся к запросу;
      пустой список               — хотя бы один движок честно ответил «ничего»;
      [{"error": ...}]            — ни один движок не дал годного ответа.

    Пустой список, за которым стоит поломка, — это ровно та ошибка, из-за
    которой обход рынка стоял: молчание источника принималось за отсутствие
    работы. Страница, из которой шаблон не извлёк ни одной ссылки, — это НЕ
    «ничего не найдено», это «не прочитали», и она уходит в ошибки.
    """
    guard.check_action("research", "GREEN")
    import time
    errs, honest_empty = [], False
    free = [time.time() - _LAST.get(n, 0) >= MIN_GAP_SEC
            for n, _, _ in ENGINES if _COOLDOWN.get(n, 0) <= time.time()]
    if free and not any(free):
        wait = min(MIN_GAP_SEC - (time.time() - _LAST.get(n, 0))
                   for n, _, _ in ENGINES if _COOLDOWN.get(n, 0) <= time.time())
        time.sleep(max(0.0, min(wait, MIN_GAP_SEC)) + 0.1)
    for name, tpl, kind in ENGINES:
        if _COOLDOWN.get(name, 0) > time.time():
            errs.append(f"{name}: пауза после отказа")
            continue
        if time.time() - _LAST.get(name, 0) < MIN_GAP_SEC:
            errs.append(f"{name}: слишком часто, пропущен")
            continue
        _LAST[name] = time.time()
        try:
            body = _get(tpl.format(q=urllib.parse.quote(query)), timeout=25)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                _COOLDOWN[name] = time.time() + COOLDOWN_SEC
            errs.append(f"{name}: HTTP {e.code}")
            continue
        except Exception as e:
            errs.append(f"{name}: {type(e).__name__}")
            continue
        try:
            raw = _parse(name, kind, body)
        except ValueError:
            errs.append(f"{name}: ответ не разобран")
            continue
        if not raw:
            # Проверяем ПОСЛЕ разбора: слово «captcha» встречается и в скриптах
            # обычной страницы с результатами, и по нему одному судить нельзя.
            if _HUMAN_CHECK.search(body or ""):
                _COOLDOWN[name] = time.time() + COOLDOWN_SEC * 2
                errs.append(f"{name}: проверка «человек ли вы» — не решаем, пауза")
                continue
            if kind == "json":
                honest_empty = True          # API сказал «ничего» явно
            else:
                errs.append(f"{name}: разметка не разобрана")
            continue
        out, seen = [], set()
        for href, title in raw:
            url = _unwrap(href)
            title = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", title)).strip()
            if not url.startswith("http") or url in seen:
                continue
            if not _relevant(query, url, title):
                continue
            seen.add(url)
            out.append({"url": url, "title": title, "engine": name})
            if len(out) >= limit:
                break
        if out:
            return out
        errs.append(f"{name}: выдача не относится к запросу — похоже на подмену")
    if honest_empty:
        return []
    return [{"error": "поиск не выполнен: " + "; ".join(errs)}]


def fetch_and_store(url):
    """Fetch a page, strip to text, store a source row. Returns (source_id, text)."""
    guard.check_action("research", "GREEN")
    html = _get(url)
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S|re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    title_m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S|re.I)
    title = re.sub(r"\s+", " ", title_m.group(1)).strip() if title_m else url
    con = connect()
    cur = con.execute(
        "INSERT INTO sources(url,title,fetched_at,content_hash,raw_excerpt) VALUES (?,?,?,?,?)",
        (url, title[:200], now(), hashlib.sha256(text.encode()).hexdigest()[:16], text[:4000]))
    con.commit()
    return cur.lastrowid, text

def extract(question, text):
    """MECHANICAL only. Router forbids this path from making judgments."""
    prompt = (f"From the text below, extract ONLY facts that answer: {question}\n"
              f"Rules: quote or closely paraphrase the text. If the text does not "
              f"answer it, reply exactly: NOT FOUND. No opinions, no guesses.\n\n"
              f"TEXT:\n{text[:6000]}")
    try:
        return router.run("extract", prompt)
    except router.EscalationRequired as e:
        return f"ESCALATE: {e}"

def record(claim, source_id, confidence=0.7):
    if memory.seen_claim(claim):
        return None
    """confidence обязателен. Утверждение без оценки уверенности - это мнение,
    выданное за факт. Умолчание 0.7 = 'извлечено из источника, но не перепроверено'."""
    con = connect()
    cur = con.execute(
        "INSERT INTO evidence(claim,source_id,agent,created_at,confidence) VALUES (?,?,?,?,?)",
        (claim, source_id, NAME, now(), confidence))
    con.commit()
    return cur.lastrowid

def run_job(question, queries, max_pages=4):
    """Full Scout cycle. Returns evidence ids."""
    con = connect()
    run_id = con.execute("INSERT INTO runs(agent,started_at,status,notes) VALUES (?,?,?,?)",
                         (NAME, now(), "running", question)).lastrowid
    con.commit()
    found, pages = [], 0
    for q in queries:
        for hit in search(q):
            if pages >= max_pages: break
            if "error" in hit:
                print(f"  search error: {hit['error']}"); continue
            url = hit["url"]
            if not url.startswith("http"): continue
            try:
                sid, text = fetch_and_store(url)
            except Exception as e:
                print(f"  SKIP {url[:60]} ({type(e).__name__})"); continue
            pages += 1
            print(f"  [{pages}] fetched {url[:70]}")
            ans = extract(question, text)
            short = ans.strip().replace("\n", " ")[:400]
            if short.upper().startswith("NOT FOUND"):
                print(f"      -> nothing relevant")
                continue
            eid = record(short, sid)
            found.append(eid)
            print(f"      -> evidence #{eid}: {short[:150]}")
        if pages >= max_pages: break
    con.execute("UPDATE runs SET ended_at=?, status=? WHERE id=?",
                (now(), f"done, {len(found)} evidence", run_id))
    con.commit()
    return found

# ---- targeted extraction: find relevant passages instead of blind truncation ----
def relevant_chunks(text, keywords, window=700, max_chunks=6):
    """Deterministic pre-filter. Cheaper and more accurate than trusting the model
    to find a needle in 900KB. Returns passages around keyword hits."""
    hits, low = [], text.lower()
    for kw in keywords:
        start = 0
        while len(hits) < max_chunks:
            i = low.find(kw.lower(), start)
            if i < 0: break
            hits.append(text[max(0, i-window//2): i+window])
            start = i + len(kw)
    return hits

def investigate(url, question, keywords):
    """Fetch one authoritative source and extract only keyword-relevant passages."""
    sid, text = fetch_and_store(url)
    chunks = relevant_chunks(text, keywords)
    if not chunks:
        print(f"  {url[:60]} -> no keyword hits ({', '.join(keywords)})")
        return sid, None
    joined = "\n---\n".join(chunks)[:6000]
    ans = extract(question, joined).strip().replace("\n", " ")
    print(f"  {url[:60]}\n    hits={len(chunks)} -> {ans[:300]}")
    if ans.upper().startswith("NOT FOUND") or ans.upper().startswith("ESCALATE"):
        return sid, None
    return sid, record(ans[:500], sid)


# ---- RELIABLE RESEARCH ----------------------------------------------------
# Free web search by scraping is NOT viable (measured 2026-09-10: DuckDuckGo
# returns a 202 challenge and intermittently fails TLS; Brave and Bing render
# results in JS and expose zero external anchors to a plain fetch). A search API
# costs money, which breaks the $0 claim. So Scout researches the sources that
# ARE reliable and are what this project actually needs: the live market index.
def research_index(query, limit=8):
    """Search our crawled Bazaar catalog. Always available, no network needed."""
    guard.check_action("research", "GREEN")
    idx = Path(__file__).resolve().parent.parent / "data" / "bazaar_index.json"
    try:
        items = json.loads(idx.read_text(encoding="utf-8"))
    except Exception as e:
        return {"error": f"index unavailable: {type(e).__name__}"}
    terms = [t for t in query.lower().split() if t]
    hits = []
    for it in items:
        a = (it.get("accepts") or [{}])[0]
        q = it.get("quality") or {}
        blob = " ".join(str(x) for x in [it.get("serviceName"), it.get("description"),
                        " ".join(it.get("tags") or []), it.get("resource")] if x).lower()
        rel = sum(1 for t in terms if t in blob)
        if not rel:
            continue
        hits.append({"name": it.get("serviceName") or it.get("resource", "")[:50],
                     "resource": it.get("resource", ""),
                     "calls30d": q.get("l30DaysTotalCalls") or 0,
                     "payers30d": q.get("l30DaysUniquePayers") or 0,
                     "rel": rel})
    hits.sort(key=lambda h: (-h["rel"], -h["payers30d"], -h["calls30d"]))
    top = hits[:limit]
    if top:
        con = connect()
        sid = con.execute("INSERT INTO sources(url,title,fetched_at,raw_excerpt) VALUES (?,?,?,?)",
                          ("index://bazaar", f"index research: {query}", now(),
                           json.dumps(top)[:1500])).lastrowid
        con.commit(); con.close()
        summary = (f"Index research '{query}': {len(hits)} matches of {len(items)}. "
                   f"Top by payers: " + "; ".join(
                       f"{h['name'][:34]} ({h['payers30d']} payers/{h['calls30d']} calls)"
                       for h in top[:4]))
        record(summary, sid, confidence=0.85)
    return {"query": query, "matches": len(hits), "top": top}
