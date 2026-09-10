"""SCOUT — the first agent. Searches, fetches, extracts, and files evidence.
Every claim it makes MUST carry a source row, per DECISION_PROTOCOL.md.
Scout is a GREEN agent: research only, reversible, private, free."""
import sys, os, re, json, hashlib, urllib.request, urllib.parse, urllib.error
from pathlib import Path
from datetime import datetime, timezone
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.db import connect
from core import router, guard

UA = "Mozilla/5.0 (compatible; P0-scout/0.1)"
NAME = "scout"

def now(): return datetime.now(timezone.utc).isoformat()

def _get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA,
          "Accept": "text/html,application/xhtml+xml"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")

def search(query, limit=8):
    """Free search, no API key. DuckDuckGo returns a 202 challenge from this network
    and intermittently fails TLS, so try engines in order and degrade gracefully
    instead of throwing. Measured 2026-09-10: bing/brave return real markup."""
    guard.check_action("research", "GREEN")
    engines = [
        ("https://search.brave.com/search?q=", r'<a[^>]+href="(https?://[^"]+)"[^>]*>\s*<div[^>]*>(.*?)</div>'),
        ("https://www.bing.com/search?q=", r'<h2><a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>'),
        ("https://html.duckduckgo.com/html/?q=", r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>'),
    ]
    html, pattern, errs = None, None, []
    for base, pat in engines:
        try:
            html = _get(base + urllib.parse.quote(query)); pattern = pat
            if html and len(html) > 40000:
                break
        except Exception as e:
            errs.append(f"{base.split('/')[2]}:{type(e).__name__}")
            html = None
    if not html:
        return [{"error": "all engines failed: " + ", ".join(errs)}]
    out, seen = [], set()
    for m in re.finditer(pattern, html, re.S):
        href = m.group(1); title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        if href in seen or "microsoft.com/bing" in href:
            continue
        seen.add(href); out.append({"url": href, "title": title})
        if len(out) >= limit:
            break
    return out

def _legacy_search_unused(query, limit=8):
    url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
    try:
        html = _get(url)
    except Exception as e:
        return [{"error": f"{type(e).__name__}: {e}"}]
    out, seen = [], set()
    for m in re.finditer(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, re.S):
        href, title = m.group(1), re.sub(r"<[^>]+>", "", m.group(2)).strip()
        if "uddg=" in href:
            href = urllib.parse.unquote(href.split("uddg=")[1].split("&")[0])
        if href in seen: continue
        seen.add(href); out.append({"url": href, "title": title})
        if len(out) >= limit: break
    return out

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

def extract(question, text, source_id):
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
            ans = extract(question, text, sid)
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
    ans = extract(question, joined, sid).strip().replace("\n", " ")
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
