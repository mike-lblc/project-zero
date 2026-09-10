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
from datetime import datetime, timezone, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.db import connect, ensure_schema
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
  rivals INTEGER DEFAULT 0,
  payout TEXT,
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
    ensure_schema(c, SCHEMA)
    return c


_API_TROUBLE = []          # накопленные отказы API за текущий заход


def _gh(args, timeout=40, expect_missing=False):
    """Вызов gh. Отказ API запоминается, а не выдаётся за пустой ответ.

    expect_missing=True — для запросов, где «нет такого файла» это нормальный
    ответ, а не сбой (мы наугад щупаем CONTRIBUTING.md по трём путям). Без
    этого различия ожидаемый 404 объявлял весь заход несостоявшимся.

    Раньше любая ошибка превращалась в "", и заход рапортовал «работы нет»,
    хотя на самом деле GitHub просто отказал по лимиту поиска (30 запросов
    в минуту). Молчание, выданное за отсутствие работы, — это ровно тот
    вид вранья, который спецификация запрещает.
    """
    try:
        r = subprocess.run(["gh"] + args, capture_output=True, text=True, timeout=timeout)
    except (subprocess.SubprocessError, OSError) as e:
        _API_TROUBLE.append(type(e).__name__)
        return ""
    if r.returncode == 0:
        return r.stdout
    err = (r.stderr or "") + (r.stdout or "")
    if expect_missing and "Not Found" in err:
        return ""
    if "rate limit" in err.lower():
        _API_TROUBLE.append("лимит запросов GitHub исчерпан")
    elif "403" in err or "401" in err:
        _API_TROUBLE.append("доступ к API отклонён")
    else:
        _API_TROUBLE.append(err.strip()[:60] or "неизвестная ошибка gh")
    return ""


def competition(repo, issue_number, title):
    """Сколько человек УЖЕ делают эту задачу и не выплачена ли она.

    Возвращает (соперники, выплачена_ли).

    Первая версия искала открытые PR, чьи заголовки содержат слова из
    заголовка задачи, и на реальном примере выдала «соперников 0» для
    tscircuit#92, где в обсуждении лежало больше сотни заявок `/attempt`,
    два десятка присланных PR и комментарий бота о том, что $75 УЖЕ
    выплачены другому человеку. Заголовки PR просто не совпадали со
    словами задачи — и агент чуть не вложил работу в разобранную задачу.

    Считать надо по самому обсуждению: заявки и ссылки на PR лежат там.
    """
    if not issue_number:
        return 0, False
    out = _gh(["api", f"repos/{repo}/issues/{issue_number}/comments?per_page=100",
               "--jq", '[.[]|{u:.user.login, b:.body}] | @json'], timeout=45)
    if not out:
        return 0, False
    try:
        comments = json.loads(out.strip().split("\n")[0])
    except (json.JSONDecodeError, IndexError):
        return 0, False

    claimants, paid = set(), False
    for c in comments:
        b = (c.get("b") or "")
        low = b.lower()
        # бот площадки объявляет о выплате — задача закрыта деньгами
        if "has been awarded" in low or "you've been awarded" in low \
                or "you have been awarded" in low:
            paid = True
        if re.search(r"/attempt\b|/claim\b|/start\b|/opire try\b", low) \
                or "github.com/" in low and "/pull/" in low:
            claimants.add(c.get("u") or b[:20])
    return len(claimants), paid


# Способы выплаты и доходят ли они до РФ-резидента с кошельком.
# ЭТОТ ФИЛЬТР ВАЖНЕЕ СУММЫ. Урок повторился дважды: сначала Binance
# (рынок есть, деньги не дойдут), теперь omi ($25 есть, платят PayPal).
# Проверять «чем платят» надо ДО того, как вложена работа.
PAYOUT_OK = ("algora", "crypto", "usdc", "usdt", "eth", "wallet", "onchain",
             "gitcoin", "polar.sh", "opire")
PAYOUT_BLOCKED = ("paypal", "venmo", "zelle", "cashapp", "ach ", "wire transfer")


def payout_reachable(repo, body):
    """Дойдут ли деньги. True / False / None (неизвестно).

    None означает «не выяснили» — тогда работу вкладывать рано, но и
    отбрасывать нельзя: надо спросить у мейнтейнера.
    """
    b = (body or "").lower()
    if any(w in b for w in PAYOUT_OK):
        return True
    if any(w in b for w in PAYOUT_BLOCKED):
        return False
    # правила проекта: смотрим руководство для участников
    for path in ("docs/doc/developer/Contribution.mdx", "CONTRIBUTING.md",
                 ".github/CONTRIBUTING.md"):
        out = _gh(["api", f"repos/{repo}/contents/{path}", "--jq", ".content"],
                  timeout=25, expect_missing=True)
        if not out:
            continue
        try:
            import base64
            txt = base64.b64decode(out).decode("utf-8", "ignore").lower()
        except Exception:
            continue
        # Ищем ТОЛЬКО вокруг слов о выплате. Первая версия искала по всему
        # документу и сказала «дойдут» для проекта, где платят PayPal, —
        # потому что слово «wallet» там про носимое устройство, а не про деньги.
        zones = []
        for m in re.finditer(r"(claim\w*\s+payment|payout|get paid|bounty rules|"
                             r"claiming payment|reward)", txt):
            zones.append(txt[max(0, m.start() - 200): m.start() + 400])
        near = " ".join(zones)
        if not near:
            break
        if any(w in near for w in PAYOUT_BLOCKED):
            return False
        if any(w in near for w in PAYOUT_OK):
            return True
        break
    return None


def looks_like_real_money(body, labels):
    """Настоящая ли это оплата.

    Проверено на живых примерах:
      /bounty $75          -> команда Algora, НАСТОЯЩИЕ деньги
      «25 RTC»             -> собственный токен проекта, НЕ доллары
      тело без суммы вовсе -> тренировочная песочница (9446 автозадач)
    """
    b = (body or "").lower()
    if "/bounty" in b or "algora" in b:
        return True
    if re.search(r"\$\s?\d", b):
        return True
    if re.search(r"\d+\s*(rtc|points?|credits?|tokens?)", b):
        return False          # своя валюта, не деньги
    return False


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
    # Запросы отранжированы ИЗМЕРЕНИЕМ, а не догадкой. Замер показал:
    #   commenter:algora-pbc     38 задач — почти все настоящие, платформа платит в USDC
    #   label:"💎 Bounty"       557 задач — та же платформа, но с примесью форков и песочниц
    #   label:bounty          4 019 задач — 95% мусор: боты-радары и security-программы
    #   "/attempt #"        407 831 задача — слово встречается в любом CI-логе, бесполезно
    # Поэтому первым идёт след платёжного бота, а широкие запросы — только хвостом.
    queries = [
        'commenter:algora-pbc state:open is:issue',
        'commenter:algora-pbc state:open is:issue label:bug',
        'label:"💎 Bounty" state:open is:issue archived:false',
        'label:"💰 Bounty" state:open is:issue archived:false',
        '"/bounty" in:body state:open is:issue language:python',
        '"/bounty" in:body state:open is:issue language:typescript',
        '"/bounty" in:body state:open is:issue created:>2026-08-01',
    ]
    seen, rows = set(), []
    for q in queries:
        out = _gh(["api", "-X", "GET", "search/issues", "-f", f"q={q}",
                   "-f", "per_page=30", "-f", "sort=created", "-f", "order=desc",
                   "--jq", ".items[] | {u:.html_url, t:.title, b:(.body//\"\"|.[0:900]), "
                           "n:.number, cm:.comments, "
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
        body = d.get("b", "")
        if not looks_like_real_money(body, d.get("l", [])):
            continue                      # песочница или своя валюта — мимо
        amount = parse_amount(d["t"] + " " + body)
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

        # ДОЙДУТ ЛИ ДЕНЬГИ — проверяем ДО оценки работы
        pay = payout_reachable(repo, body)
        if pay is False:
            continue          # платят способом, недоступным владельцу

        # ЗАНЯТОСТЬ: сколько уже делают то же самое и не выплачено ли уже
        rivals, paid = competition(repo, d.get("n"), d["t"])
        if paid:
            continue                      # премию уже получил другой — работать не за что
        if rivals >= 4:
            continue                      # задача фактически разобрана
        stack_fit = OUR_STACK.get(lang, 0.25)
        # доверие к репозиторию: звёзды сглаженно
        import math
        trust = min(1.0, math.log10(1 + stars) / 2.4)
        # чем больше соперников и обсуждения, тем ниже шанс, что возьмут именно нас
        crowd = 1.0 / (1 + rivals * 0.8 + (d.get("cm", 0) or 0) * 0.02)
        # неизвестный способ выплаты — не запрет, но и не повод вкладываться
        pay_factor = 1.0 if pay is True else 0.35
        fit = round(amount * stack_fit * (0.35 + 0.65 * trust) * crowd * pay_factor, 1)
        out.append({"url": d["u"], "repo": repo, "title": d["t"][:180],
                    "amount": amount, "stars": stars, "language": lang, "rivals": rivals,
                    "payout": ("крипта/Algora" if pay is True else "неизвестно"),
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
    _API_TROUBLE.clear()
    raw = search(limit)
    rows = enrich_and_score(raw)
    # Отказ API — единственный случай, когда заход НЕЛЬЗЯ считать проведённым:
    # мы не видели рынок, значит и снимать задачи с очереди не имеем права.
    if _API_TROUBLE and not rows:
        why = ", ".join(sorted(set(_API_TROUBLE))[:3])
        bus.broadcast("bounty", f"Заход НЕ СОСТОЯЛСЯ: API отказал ({why}). "
                                f"Это не «работы нет» — это «я не смог посмотреть». "
                                f"Повторю в следующем цикле.")
        return f"поиск не выполнен: {why}"
    c = _con()
    for r in rows:
        c.execute("""INSERT INTO bounties(url,repo,title,amount_usd,currency,stars,language,
                     labels,fit_score,rivals,payout,found_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                     ON CONFLICT(url) DO UPDATE SET amount_usd=?, fit_score=?, stars=?,
                     rivals=?, status='found'""",
                  (r["url"], r["repo"], r["title"], r["amount"], "USD", r["stars"],
                   r["language"], r["labels"], r["fit"], r.get("rivals", 0),
                   r.get("payout"), now(),
                   r["amount"], r["fit"], r["stars"], r.get("rivals", 0)))
    # ЧИСТКА. Задача, не прошедшая фильтры в этот заход, больше не «доступная»:
    # премию могли выплатить, толпа могла набежать. Оставлять её в очереди —
    # значит однажды вложить работу в разобранное. Поймано на tscircuit#92:
    # 72 заявки и уже выплаченные $75, а в базе висело «соперников 0».
    alive = {r["url"] for r in rows}
    stale = c.execute("SELECT url,repo FROM bounties WHERE status='found'").fetchall()
    dropped = 0
    for url, repo in stale:
        if url not in alive:
            c.execute("UPDATE bounties SET status='lost', note=? WHERE url=?",
                      ("не прошла повторную проверку: разобрана, выплачена или недостижима", url))
            dropped += 1
    c.commit()
    tot = c.execute("SELECT COUNT(*) FROM bounties WHERE status='found'").fetchone()[0]
    money = c.execute("SELECT COALESCE(SUM(amount_usd),0) FROM bounties "
                      "WHERE status='found'").fetchone()[0]
    top = c.execute("SELECT title,amount_usd,repo FROM bounties WHERE status='found' "
                    "ORDER BY fit_score DESC LIMIT 1").fetchone()
    c.close()
    if not tot:
        bus.broadcast("bounty", f"Просмотрел {len(raw)} открытых задач: доступных не осталось, "
                                f"{dropped} снято с очереди как разобранные или уже "
                                f"выплаченные. Рынок баунти забит конкурирующими агентами — "
                                f"нужен менее людный источник работы.")
        return f"просмотрено {len(raw)}, доступных нет, снято {dropped}"
    bus.broadcast("bounty", f"Доступных задач: {tot}, суммарно ${money:.0f}"
                            + (f", снято с очереди {dropped}" if dropped else "")
                            + f". Лучшая: {top[2]} — ${top[1]:.0f}.")
    return f"доступно {tot} на ${money:.0f}, снято {dropped}"


def shortlist(n=10):
    c = _con()
    rows = c.execute("""SELECT url,repo,title,amount_usd,stars,language,fit_score
                        FROM bounties WHERE status='found'
                        ORDER BY fit_score DESC LIMIT ?""", (n,)).fetchall()
    c.close()
    return [dict(zip(("url", "repo", "title", "amount", "stars", "language", "fit"), r))
            for r in rows]


def fresh_bounties(max_age_hours=6, max_rivals=3):
    """Перехват СВЕЖИХ премий — пока на них не набежала толпа.

    Почему это отдельный инструмент, а не настройка охотника. Замер живого
    рынка показал жёсткую картину: на задаче tscircuit#92 за $75 висит
    72 заявки и два десятка присланных PR, и премия уже выплачена. Полный
    обход рынка вернул НОЛЬ доступных задач из шестидесяти просмотренных.
    Рынок не пустой — он разбирается за часы конкурирующими агентами.

    Значит единственное доступное нам преимущество — не качество разбора
    и не размер суммы, а СКОРОСТЬ. Задача, которой шесть часов и на которой
    ещё нет трёх заявок, — единственная, где у нас есть шанс быть первыми.

    Проверка идёт часто и стоит один запрос поиска: за квоту можно не бояться.
    """
    guard.check_action("research", "GREEN")
    _API_TROUBLE.clear()
    since = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    q = f"commenter:algora-pbc state:open is:issue created:>{since.strftime('%Y-%m-%dT%H:%M:%SZ')}"
    out = _gh(["api", "-X", "GET", "search/issues", "-f", f"q={q}",
               "-f", "per_page=25", "-f", "sort=created", "-f", "order=desc",
               "--jq", '.items[] | {u:.html_url, t:.title, b:(.body//""|.[0:900]), '
                       'n:.number, cm:.comments, created:.created_at, '
                       'r:(.repository_url|split("/")|.[-2:]|join("/")), '
                       'l:[.labels[].name]} | @json'])
    if not out:
        if _API_TROUBLE:
            return f"перехват не выполнен: {', '.join(sorted(set(_API_TROUBLE))[:2])}"
        return f"свежих премий за {max_age_hours} ч нет"

    hot = []
    for line in out.strip().split("\n"):
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not looks_like_real_money(d.get("b", ""), d.get("l", [])):
            continue
        amount = parse_amount(d["t"] + " " + d.get("b", ""))
        if not amount:
            continue
        rivals, paid = competition(d["r"], d["n"], d["t"])
        if paid or rivals > max_rivals:
            continue
        hot.append({"url": d["u"], "repo": d["r"], "title": d["t"][:180],
                    "amount": amount, "rivals": rivals, "created": d.get("created")})

    if not hot:
        return f"свежих премий за {max_age_hours} ч нет (проверено, толпа везде)"

    hot.sort(key=lambda h: (h["rivals"], -h["amount"]))
    c = _con()
    for h in hot:
        c.execute("""INSERT INTO bounties(url,repo,title,amount_usd,currency,rivals,
                     status,note,found_at) VALUES (?,?,?,?,?,?,'found',?,?)
                     ON CONFLICT(url) DO UPDATE SET rivals=?, status='found'""",
                  (h["url"], h["repo"], h["title"], h["amount"], "USD", h["rivals"],
                   f"перехвачена свежей: возраст до {max_age_hours} ч", now(), h["rivals"]))
    c.commit(); c.close()
    top = hot[0]
    bus.broadcast("bounty", f"СВЕЖАЯ ПРЕМИЯ, толпы ещё нет: {top['repo']} за ${top['amount']:.0f}, "
                            f"заявок {top['rivals']}. {top['title'][:70]}. "
                            f"Здесь решает скорость — берём сейчас или не берём вовсе.")
    return f"перехвачено свежих: {len(hot)}, лучшая ${top['amount']:.0f} при {top['rivals']} заявках"


CYCLE = [("hunt_bounties", lambda: hunt(40))]
FAST_CYCLE = [("fresh_bounties", lambda: fresh_bounties(6, 3))]


if __name__ == "__main__":
    print(hunt())
    print()
    print("═══ ЛУЧШИЕ ПО ПРИГОДНОСТИ ═══")
    for b in shortlist(12):
        print(f"  ${b['amount']:>7.0f}  оценка {b['fit']:>7.1f}  {b['language'][:10]:10} "
              f"звёзд {b['stars']:>5}  {b['repo'][:28]:28}")
        print(f"           {b['title'][:88]}")
