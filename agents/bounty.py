"""ОХОТНИК ЗА БАУНТИ — работа, за которую платят живые деньги.

Почему это найдено так поздно: я всё время искал, КОМУ ПРОДАТЬ, и ни разу не
посмотрел, ГДЕ ПЛАТЯТ ЗА РАБОТУ НАПРЯМУЮ.

Порядок величин, ради которого стоит развернуться:
    рынок x402, лучшая ниша .......... $2.61 на поставщика в МЕСЯЦ
    один баунти ...................... $50 – $2500 за ЗАДАЧУ
Один баунти в $500 равен двумстам годам медианной выручки на x402.

Почему это подходит именно нам:
    * платят за КОД — ровно то, что мы умеем
    * никаких холодных писем: решил задачу, прислал PR, получил деньги
    * оплата часто криптой на кошелёк, то есть рельс тот же, что уже проверен
    * `gh` уже авторизован, форк и PR доступны прямо сейчас

Что здесь честно ограничено:
    * берём только задачи, где сумма указана ЯВНО в заголовке или теле
    * отсеиваем мусорные репозитории (нулевые звёзды + свежесозданные + серии
      однотипных «баунти» — типичная накрутка)
    * не обещаем выплату: она зависит от площадки и от слияния PR
"""
import sys, re, json, subprocess
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.db import connect
from core import guard, bus

ROOT = Path(__file__).resolve().parent.parent

SCHEMA = """
CREATE TABLE IF NOT EXISTS bounties (
  id INTEGER PRIMARY KEY,
  url TEXT NOT NULL UNIQUE,
  repo TEXT NOT NULL,
  title TEXT NOT NULL,
  amount_usd REAL,
  currency TEXT,
  stars INTEGER,
  language TEXT,
  labels TEXT,
  fit_score REAL,
  status TEXT NOT NULL DEFAULT 'found',   -- found | shortlisted | attempted | won | lost
  note TEXT,
  found_at TEXT NOT NULL
);
"""

# Языки и темы, где мы реально можем сделать работу, а не сделать вид
OUR_STACK = {"python": 1.0, "javascript": 0.9, "typescript": 0.9, "shell": 0.8,
             "html": 0.7, "sql": 0.8, "go": 0.5, "rust": 0.35, "c": 0.2, "c++": 0.2,
             "java": 0.4, "solidity": 0.4}

# Признаки фермы баунти. Первая версия отсекала только репозитории без звёзд,
# и пропустила ферму с 8 звёздами, где ОДИН репозиторий держал пять «задач»
# на $500-1250 — то есть 74% найденной суммы оказалось мусором.
SPAM_HINTS = ("bounty-", "-bounty", "bounties", "airdrop", "reward-hub",
              "-test", "test-", "playground-farm", "radar")
MIN_STARS_FOR_BIG = 40      # крупная сумма от малоизвестного репозитория недостоверна
BIG_AMOUNT = 200.0
MAX_PER_REPO = 2            # ферма выдаёт десятки однотипных задач из одного места

AMOUNT_RE = [
    (re.compile(r"\$\s?([\d,]+(?:\.\d+)?)\s*(?:k\b)?", re.I), 1.0),
    (re.compile(r"([\d,]+(?:\.\d+)?)\s*USDC?\b", re.I), 1.0),
    (re.compile(r"bounty[:\s]+\$?\s?([\d,]+)", re.I), 1.0),
]


def now():
    return datetime.now(timezone.utc).isoformat()


def _con():
    c = connect()
    c.executescript(SCHEMA)
    return c


def _gh(args, timeout=40):
    try:
        r = subprocess.run(["gh"] + args, capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


def parse_amount(text):
    """Сумма из заголовка или тела. Берём максимум, но не верим абсурду."""
    best = 0.0
    for rx, mult in AMOUNT_RE:
        for m in rx.finditer(text or ""):
            try:
                v = float(m.group(1).replace(",", "")) * mult
            except Exception:
                continue
            if 20 <= v <= 50000:          # ниже 20 не окупает время, выше 50k — почти всегда мусор
                best = max(best, v)
    return best or None


def search(limit=60):
    """Ищет открытые оплачиваемые задачи по нашему стеку."""
    guard.check_action("research", "GREEN")
    queries = [
        'label:"💎 Bounty" state:open language:python',
        'label:"💎 Bounty" state:open language:typescript',
        'label:bounty state:open language:python',
        'label:bounty state:open language:javascript',
        'label:"help wanted" bounty in:body state:open language:python',
    ]
    seen, rows = set(), []
    for q in queries:
        out = _gh(["api", "-X", "GET", "search/issues", "-f", f"q={q}",
                   "-f", "per_page=25", "-f", "sort=updated",
                   "--jq", ".items[] | {u:.html_url, t:.title, b:(.body//\"\"|.[0:400]), "
                           "r:(.repository_url|split(\"/\")|.[-2:]|join(\"/\")), "
                           "l:[.labels[].name]} | @json"])
        for line in (out or "").strip().split("\n"):
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d["u"] in seen:
                continue
            seen.add(d["u"])
            rows.append(d)
    return rows[:limit]


def enrich_and_score(rows):
    """Достаёт сумму, звёзды репозитория и считает пригодность."""
    out = []
    repo_cache = {}
    for d in rows:
        amount = parse_amount(d["t"] + " " + d.get("b", ""))
        if not amount:
            continue
        repo = d["r"]
        if repo not in repo_cache:
            info = _gh(["api", f"repos/{repo}",
                        "--jq", "{s:.stargazers_count,l:(.language//\"\"),c:.created_at,f:.forks_count}"])
            try:
                repo_cache[repo] = json.loads(info) if info else {}
            except Exception:
                repo_cache[repo] = {}
        ri = repo_cache[repo]
        stars = ri.get("s", 0) or 0
        lang = (ri.get("l") or "").lower()

        # отсев ферм: имя ради баунти, либо крупная сумма при малой известности
        low = repo.lower()
        if any(h in low for h in SPAM_HINTS) and stars < MIN_STARS_FOR_BIG:
            continue
        if amount >= BIG_AMOUNT and stars < MIN_STARS_FOR_BIG:
            continue          # $1250 от репозитория с 8 звёздами — почти всегда мусор

        stack_fit = OUR_STACK.get(lang, 0.25)
        # доверие к репозиторию: звёзды сглаженно
        import math
        trust = min(1.0, math.log10(1 + stars) / 2.4)
        fit = round(amount * stack_fit * (0.35 + 0.65 * trust), 1)
        out.append({"url": d["u"], "repo": repo, "title": d["t"][:180],
                    "amount": amount, "stars": stars, "language": lang,
                    "labels": ",".join(d.get("l", []))[:120], "fit": fit})
    out.sort(key=lambda r: -r["fit"])
    # не больше MAX_PER_REPO задач из одного репозитория: ферма иначе забьёт весь список
    seen_repo, capped = {}, []
    for r in out:
        k = r["repo"]
        seen_repo[k] = seen_repo.get(k, 0) + 1
        if seen_repo[k] <= MAX_PER_REPO:
            capped.append(r)
    return capped


def hunt(limit=60):
    """Полный заход: найти, оценить, записать."""
    rows = enrich_and_score(search(limit))
    if not rows:
        bus.broadcast("bounty", "Оплачиваемых задач по нашему стеку сейчас не нашёл.")
        return "ничего не найдено"
    c = _con()
    for r in rows:
        c.execute("""INSERT INTO bounties(url,repo,title,amount_usd,currency,stars,language,
                     labels,fit_score,found_at) VALUES (?,?,?,?,?,?,?,?,?,?)
                     ON CONFLICT(url) DO UPDATE SET amount_usd=?, fit_score=?, stars=?""",
                  (r["url"], r["repo"], r["title"], r["amount"], "USD", r["stars"],
                   r["language"], r["labels"], r["fit"], now(),
                   r["amount"], r["fit"], r["stars"]))
    c.commit()
    tot = c.execute("SELECT COUNT(*) FROM bounties").fetchone()[0]
    money = c.execute("SELECT COALESCE(SUM(amount_usd),0) FROM bounties").fetchone()[0]
    top = c.execute("SELECT title,amount_usd,repo FROM bounties ORDER BY fit_score DESC LIMIT 1").fetchone()
    c.close()
    bus.broadcast("bounty", f"Найдено оплачиваемых задач: {tot}, суммарно ${money:.0f}. "
                            f"Лучшая по пригодности: {top[2]} — ${top[1]:.0f}. "
                            f"Это чек за задачу, а не центы за вызов.")
    return f"задач {tot}, суммарно ${money:.0f}"


def shortlist(n=10):
    c = _con()
    rows = c.execute("""SELECT url,repo,title,amount_usd,stars,language,fit_score
                        FROM bounties WHERE status='found'
                        ORDER BY fit_score DESC LIMIT ?""", (n,)).fetchall()
    c.close()
    return [dict(zip(("url", "repo", "title", "amount", "stars", "language", "fit"), r))
            for r in rows]


CYCLE = [("hunt_bounties", lambda: hunt(40))]


if __name__ == "__main__":
    print(hunt())
    print()
    print("═══ ЛУЧШИЕ ПО ПРИГОДНОСТИ ═══")
    for b in shortlist(12):
        print(f"  ${b['amount']:>7.0f}  оценка {b['fit']:>7.1f}  {b['language'][:10]:10} "
              f"звёзд {b['stars']:>5}  {b['repo'][:28]:28}")
        print(f"           {b['title'][:88]}")
