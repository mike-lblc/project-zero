"""ДИЛЕР — сеть путей к первому платежу. Один агент, весь рынок, а не одна площадка.

Спецификация — MTBX.txt («протокол последней транзакции»), с решениями владельца
от 15.09.2026, которые её уточняют и записаны в docs/MTBX_DECISIONS.md:
  * платить может КТО УГОДНО — другой агент или человек; засчитывается любой
    подтверждённый перевод от постороннего;
  * дедлайна и самоуничтожения нет: агент работает, пока платёж не пришёл,
    и после него — пока не придёт следующий;
  * кошельки настоящие, проверка — по хешу в сети, а не по словам;
  * на площадках, где адрес кошелька в тексте запрещён, действует обходной
    путь: адрес несёт ответ 402 нашего x402-сервиса, а в криптосабмолтах
    Moltbook адрес пишется прямо — там он живёт месяцами (замер 15.09).

Цикл по спецификации: OBSERVE → MODEL → PRIORITIZE → ACT → VERIFY → LEARN,
после каждого оборота — состояние в базе (раздел IX).

ЧЕГО ЗДЕСЬ НЕТ, И ПОЧЕМУ. Дилер не дублирует обращения продавца, заявки
мастерового и посты канала: у каждого из них свои пределы и своя история
«один адресат — одно письмо». Дилер ведёт СЕТЬ: реестр каналов (где вообще
можно получить деньги без вложений и новых аккаунтов), реестр контрагентов
(кто может заплатить и через что), приоритет, собственные обращения там, где
никто другой не действует (агенты Moltbook с деньгами), делегирование там,
где действует другой агент, сверку поступлений с контрагентами и обучение
на ответах. Сообщения посторонних — данные, не команды (раздел VII).
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.db import connect, ensure_schema  # noqa: E402
from core import guard, bus, payment  # noqa: E402

SERVICE_URL = "https://x402-bazaar-rank.x402-bazaar-rank-worker.workers.dev"
RESPONSE_WINDOW_H = 72          # срок ответа в каждом обращении (раздел VIII.5)
MAX_MESSAGES_PER_CYCLE = 2      # параллельность без спама: пределы CAPS остаются
MIN_ASK = {"base": ("0.01", "USDC"), "stacks": ("150", "sats sBTC")}

SCHEMA = """
CREATE TABLE IF NOT EXISTS channels (
  id INTEGER PRIMARY KEY,
  key TEXT NOT NULL UNIQUE,            -- github_issues, moltbook:usdc, aibtc_board, x402_endpoint, path:<platform>
  platform TEXT NOT NULL,
  kind TEXT NOT NULL,                  -- public_thread | agent_board | inbound_paid_api | listing | bounty_issues | task_board | path
  url TEXT,
  how TEXT,                            -- как именно через него получают деньги
  needs_account INTEGER,               -- 0 есть/не нужен, 1 нужен новый (BLACK), NULL неизвестно
  allows_wallet_address INTEGER,       -- 1 адрес в тексте живёт, 0 удаляется, NULL не мерили
  allows_links INTEGER,
  alive INTEGER,                       -- 1 жив, 0 мёртв, NULL не проверяли
  evidence TEXT,
  discovered_at TEXT NOT NULL,
  checked_at TEXT,
  uses INTEGER DEFAULT 0,
  results INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS counterparties (
  id INTEGER PRIMARY KEY,
  handle TEXT NOT NULL,
  platform TEXT NOT NULL,
  kind TEXT NOT NULL,                  -- agent | human | org | unknown
  source TEXT,                         -- откуда узнали: leads | moltbook_demand | moltbook_inbound | bounties | aibtc | gateway | receipt
  source_ref TEXT,                     -- пост, issue, домен
  wallet TEXT,
  funds_signal REAL DEFAULT 0,         -- 0..1: есть ли у него деньги, по свидетельствам
  response_p REAL DEFAULT 0,           -- 0..1: вероятность ответа, по истории и приору площадки
  decision_speed_h REAL,               -- ожидаемое время решения, часов
  needs TEXT,                          -- что ему нужно (его слова)
  cooperation REAL DEFAULT 0.5,
  reliability REAL DEFAULT 0.5,
  expected_tx_h REAL,
  priority REAL DEFAULT 0,
  status TEXT DEFAULT 'new',           -- new | contacted | replied | delegated:<agent> | paid | dead
  contacts INTEGER DEFAULT 0,
  replies INTEGER DEFAULT 0,
  objections TEXT,                     -- JSON-список
  last_contact_at TEXT,
  first_seen TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(platform, handle)
);
CREATE TABLE IF NOT EXISTS dealer_attempts (
  id INTEGER PRIMARY KEY,
  counterparty_id INTEGER,
  channel_key TEXT,
  message_ref TEXT,                    -- id комментария / URL — доказательство отправки
  ask_amount TEXT,
  ask_currency TEXT,
  ask_network TEXT,
  deadline_at TEXT,
  outcome TEXT DEFAULT 'sent',         -- sent | replied | refused | paid | expired | delegated
  objection TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dealer_state (
  id INTEGER PRIMARY KEY,
  at TEXT NOT NULL,
  state TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dealer_strategies (
  name TEXT PRIMARY KEY,
  tries INTEGER DEFAULT 0,
  successes INTEGER DEFAULT 0,
  last_at TEXT,
  note TEXT
);
CREATE TABLE IF NOT EXISTS dealer_receipts (
  proof TEXT PRIMARY KEY,
  from_party TEXT,
  network TEXT,
  currency TEXT,
  amount REAL,
  sender_kind TEXT,                    -- agent | human | org | unknown
  counterparty_id INTEGER,
  seen_at TEXT NOT NULL
);
"""


def now():
    return datetime.now(timezone.utc).isoformat()


def _con():
    c = connect()
    ensure_schema(c, SCHEMA)
    return c


def _tables(c):
    """Какие таблицы есть. Спрашивать базу дешевле, чем ловить исключение: пойманное
    внутри функции исключение держит её кадр и соединение до сборки мусора."""
    return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}


# ═══════════════════════════════════════════ OBSERVE: каналы
# Приоры площадок: за сколько часов там обычно решают и платят. Это не
# измерение, а стартовое допущение; LEARN его двигает по фактам.
PLATFORM_PRIOR = {
    "aibtc":    {"response_p": 0.35, "decision_speed_h": 48,  "expected_tx_h": 72},
    "moltbook": {"response_p": 0.25, "decision_speed_h": 72,  "expected_tx_h": 96},
    "github":   {"response_p": 0.20, "decision_speed_h": 120, "expected_tx_h": 240},
    "x402":     {"response_p": 0.10, "decision_speed_h": 24,  "expected_tx_h": 24},
    "gateway":  {"response_p": 0.15, "decision_speed_h": 72,  "expected_tx_h": 120},
}

KNOWN_CHANNELS = [
    ("github_issues", "github", "public_thread", "https://github.com",
     "issue или комментарий в публичном репозитории адресата от аккаунта владельца; "
     "адрес кошелька и ссылки разрешены", 0, 1, 1),
    ("aibtc_board", "aibtc", "agent_board", "https://aibtc.com/bounties",
     "сдача работы подписью зарегистрированного агента (core.aibtc.submit); выплата sBTC "
     "на STX-адрес агента автоматически", 0, 1, 1),
    ("x402_endpoint", "x402", "inbound_paid_api", SERVICE_URL,
     "покупатель платит за вызов; адрес получателя несёт ответ 402; плательщик — программа", 0, 1, 1),
    ("mcp_registry", "mcp", "listing", "https://registry.modelcontextprotocol.io",
     "наш сервер в реестре MCP: обнаружимость для агентов, платежи идут через x402", 0, 0, 1),
    ("github_bounty_issues", "github", "bounty_issues", "https://github.com/search?q=label%3Abounty",
     "задачи с объявленной наградой: заявка → PR → выплата в крипте от проекта", 0, 1, 1),
    ("dework_board", "dework", "task_board", "https://app.dework.xyz",
     "чтение задач с наградой по публичному GraphQL; заявка требует входа кошельком", None, None, 1),
]
MOLTBOOK_MONEY_SUBMOLTS = ("agentcommerce", "clawtasks", "x402", "x402-billing", "usdc",
                           "agentfinance", "forhire")


def _moltbook_policy():
    """Свидетельства по сабмолтам: живут ли там адреса кошельков (замер channel_manager)."""
    try:
        c = _con()
        c.execute("CREATE TABLE IF NOT EXISTS moltbook_address_policy (submolt TEXT PRIMARY KEY, "
                  "with_address INTEGER, posts INTEGER, oldest TEXT, checked_at TEXT)")
        rows = {r[0]: (r[1], r[2], r[3], r[4]) for r in
                c.execute("SELECT submolt, with_address, posts, oldest, checked_at FROM moltbook_address_policy")}
        c.close()
        return rows
    except Exception:
        return {}


def weave():
    """OBSERVE-1: реестр каналов — где вообще можно получить деньги без вложений.

    Источники: известные каналы (проверенные делом), сабмолты Moltbook с замером
    политики адресов и пути к деньгам разведчика (money_paths), открытые для нас.
    """
    c = _con()
    stamp = now()
    added = 0
    for key, platform, kind, url, how, needs_acc, addr_ok, links_ok in KNOWN_CHANNELS:
        cur = c.execute("INSERT OR IGNORE INTO channels(key,platform,kind,url,how,needs_account,"
                        "allows_wallet_address,allows_links,alive,discovered_at) "
                        "VALUES (?,?,?,?,?,?,?,?,1,?)",
                        (key, platform, kind, url, how, needs_acc, addr_ok, links_ok, stamp))
        added += cur.rowcount
    policy = _moltbook_policy()
    for sub in MOLTBOOK_MONEY_SUBMOLTS + ("general",):
        with_addr, posts, oldest, checked = policy.get(sub, (None, None, None, None))
        allows = None if with_addr is None else (1 if with_addr > 0 else 0)
        ev = (f"постов с адресами {with_addr} из {posts}, старейший {oldest} (замер {str(checked)[:10]})"
              if with_addr is not None else "политика адресов не измерена")
        cur = c.execute("INSERT OR IGNORE INTO channels(key,platform,kind,url,how,needs_account,"
                        "allows_wallet_address,allows_links,alive,evidence,discovered_at) "
                        "VALUES (?,?,?,?,?,?,?,?,1,?,?)",
                        (f"moltbook:{sub}", "moltbook", "public_thread",
                         f"https://www.moltbook.com/m/{sub}",
                         "пост, комментарий или ответ от общей учётной записи; ссылки — только на наш "
                         "сервис; адрес кошелька — по политике сабмолта", 0, allows, 0, ev, stamp))
        added += cur.rowcount
        c.execute("UPDATE channels SET allows_wallet_address=?, evidence=?, checked_at=? WHERE key=?",
                  (allows, ev, stamp, f"moltbook:{sub}"))
    # Пути к деньгам, найденные разведчиком: только те, где выплата в крипте и
    # не нужен новый аккаунт — остальное закрыто классом BLACK или фиатом.
    rows = []
    if "money_paths" in _tables(c):
        rows = c.execute("SELECT platform, category, payout, needs_account, open_to_us, evidence "
                         "FROM money_paths WHERE payout='crypto' AND "
                         "(open_to_us=1 OR needs_account=0) ORDER BY score DESC LIMIT 60").fetchall()
    for platform, category, payout, needs_acc, open_to, ev in rows:
        cur = c.execute("INSERT OR IGNORE INTO channels(key,platform,kind,url,how,needs_account,"
                        "allows_wallet_address,allows_links,alive,evidence,discovered_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (f"path:{platform}", platform, "path", f"https://{platform}",
                         f"{category}: выплата {payout}", needs_acc, None, None, None,
                         (ev or "")[:300], stamp))
        added += cur.rowcount
    # ПОРТФЕЛЬ СТРАТЕГИЙ — несколько независимых путей к платежу одновременно (раздел IV),
    # у каждого своя доля успеха и последний запуск; пустой портфель — это труба, не сеть.
    for name, note in (("moltbook_reply", "обращение под постом агента с деньгами"),
                       ("index_listing", "быть там, где ищут покупатели: x402scan, PayAI, Bazaar, рынки фасилитаторов"),
                       ("payer_mapping", "реестр активных плательщиков x402 и их экосистем"),
                       ("escrow_tasks", "эскроу-задачи агентов с бюджетом (taskmarket.dev)"),
                       ("github_outreach", "продавцы x402 через их публичные репозитории (salesman)"),
                       ("bounty_claims", "объявленные награды GitHub (craftsman)"),
                       ("aibtc_delivery", "сдача на доску AIBTC подписью агента"),
                       ("mcp_listing", "реестр MCP: обнаружимость для агентов"),
                       ("content_posts", "предметные посты в криптосабмолтах Moltbook (channel_manager)")):
        c.execute("INSERT OR IGNORE INTO dealer_strategies(name,tries,note) VALUES (?,0,?)", (name, note))
    c.commit()
    total = c.execute("SELECT COUNT(*) FROM channels").fetchone()[0]
    alive = c.execute("SELECT COUNT(*) FROM channels WHERE alive=1").fetchone()[0]
    c.close()
    return {"channels": total, "alive": alive, "added": added}


def probe_channels(limit=4):
    """Щупает непроверенные каналы живым запросом; молчание — «неизвестно», не «мёртв»."""
    from agents import bounty
    c = _con()
    rows = c.execute("SELECT key, url FROM channels WHERE alive IS NULL AND url IS NOT NULL "
                     "ORDER BY id LIMIT ?", (limit,)).fetchall()
    c.close()
    out = {}
    for key, url in rows:
        try:
            alive = bounty.platform_alive(url)
        except Exception:
            alive = None
        out[key] = alive
        c = _con()
        c.execute("UPDATE channels SET alive=?, checked_at=? WHERE key=?",
                  (None if alive is None else int(bool(alive)), now(), key))
        c.commit(); c.close()
    return out


# ═══════════════════════════════════════════ OBSERVE: контрагенты
_MONEY = re.compile(r"\$\s?\d|\d\s?(usdc|usdt|sats?|btc|eth|sol)\b|\btip\b|\bpay(ing|s|ment)?\b|\bbounty\b|\breward\b", re.I)
_WALLET = re.compile(r"\b0x[0-9a-fA-F]{40}\b|\bbc1[ac-hj-np-z02-9]{25,90}\b|\bSP[0-9A-Z]{28,41}\b")
# ПРОСИТ ДЕНЕГ САМ — НЕ ПЛАТЕЛЬЩИК. Пост «пришлите мне пыль», «PayPal.me», «нулевой бюджет»
# сканер спроса принимает за намерение платить (там есть сумма и слово «pay»). Очередь
# суждений 14.09 сняла два таких как «не плательщик»; дилер знает это правило сам,
# чтобы не тратить на них суточный предел обращений.
_SEEKER = re.compile(r"zero budget|no budget|send (me|us|dust|a tip|sats)|paypal\.me|donat(e|ion)s?\b|"
                     r"tip ?jar|tips welcome|reward: thanks|need .{0,20}agent to send", re.I)


def _upsert(c, handle, platform, kind, source, source_ref=None, wallet=None, funds=None, needs=None):
    stamp = now()
    prior = PLATFORM_PRIOR.get(platform, PLATFORM_PRIOR["gateway"])
    row = c.execute("SELECT id, funds_signal, wallet FROM counterparties WHERE platform=? AND handle=?",
                    (platform, handle)).fetchone()
    if row:
        cid, old_funds, old_wallet = row[0], row[1] or 0, row[2]
        c.execute("UPDATE counterparties SET funds_signal=?, wallet=COALESCE(?, wallet), "
                  "needs=COALESCE(?, needs), updated_at=? WHERE id=?",
                  (max(old_funds, funds or 0), wallet, needs, stamp, cid))
        return cid, False
    cur = c.execute("INSERT INTO counterparties(handle,platform,kind,source,source_ref,wallet,"
                    "funds_signal,response_p,decision_speed_h,needs,expected_tx_h,first_seen,updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (handle, platform, kind, source, source_ref, wallet, funds or 0,
                     prior["response_p"], prior["decision_speed_h"], needs,
                     prior["expected_tx_h"], stamp, stamp))
    return cur.lastrowid, True


def survey():
    """OBSERVE-2: кто может заплатить — по всем источникам системы, а не по одному."""
    c = _con()
    new = 0
    have = _tables(c)
    # 1) продавцы x402 с публичным каналом (люди/организации): деньги у них есть — они
    #    сами получают платежи; сигнал — по нижней границе плательщиков.
    if "leads" in have:
        for dom, ch, pmin, pay, calls in c.execute(
                "SELECT domain, channel, payers_min_30d, payers_30d, calls_30d FROM leads "
                "WHERE reachable=1 AND channel IS NOT NULL AND channel<>''"):
            funds = min(1.0, 0.3 + 0.05 * float(pmin or 0)) if (pmin or pay) else 0.2
            _, fresh = _upsert(c, ch, "github", "org", "leads", dom, funds=round(funds, 3),
                               needs="сравнение своих x402-метрик с рынком")
            new += fresh
    # 2) агенты Moltbook с признаком денег: посты с намерением платить и ответы под нашими
    if "moltbook_demand" in have:
        for pid, sub, author, title, body in c.execute(
                "SELECT post_id, submolt, author, title, body FROM moltbook_demand"):
            txt = f"{title} {body}"
            w = _WALLET.search(txt)
            seeker = bool(_SEEKER.search(txt))
            funds = 0.05 if seeker else (0.8 if re.search(r"usdc|tip|sats|btc", txt, re.I) else 0.6)
            cid, fresh = _upsert(c, author, "moltbook", "agent", "moltbook_demand", pid,
                                 wallet=w.group(0) if w else None, funds=funds, needs=title[:200])
            if seeker:
                c.execute("UPDATE counterparties SET status='dead', funds_signal=0.05, objections=? "
                          "WHERE id=? AND status IN ('new','dead')",
                          (json.dumps(["просит денег сам или без бюджета — не плательщик"], ensure_ascii=False), cid))
            new += fresh
    if "moltbook_inbound" in have:
        for cid_, pid, author, body, answered in c.execute(
                "SELECT comment_id, post_id, author, body, answered_at FROM moltbook_inbound"):
            funds = 0.5 if _MONEY.search(body or "") else 0.3
            cid, fresh = _upsert(c, author, "moltbook", "agent", "moltbook_inbound", pid,
                                 funds=funds, needs=(body or "")[:200])
            if answered:
                # канал уже ответил ему под нашим постом — это и есть наше одно обращение
                c.execute("UPDATE counterparties SET status='contacted', contacts=MAX(contacts,1), "
                          "last_contact_at=COALESCE(last_contact_at,?) WHERE id=? AND status='new'",
                          (answered, cid))
            new += fresh
    # 3) проекты, объявившие награду сами (declared=1): у них есть деньги на задачу
    if "bounties" in have:
        for url, repo, title, amt in c.execute(
                "SELECT url, repo, title, amount_usd FROM bounties WHERE declared=1 "
                "AND status IN ('found','attempted') AND repo NOT LIKE '%.%'"):
            owner = (repo or "").split("/")[0]
            if not owner:
                continue
            funds = min(1.0, 0.5 + float(amt or 0) / 200.0)
            _, fresh = _upsert(c, repo, "github", "org", "bounties", url, funds=round(funds, 3),
                               needs=title[:200])
            new += fresh
        for url, title, amt, note in c.execute(
                "SELECT url, title, amount_usd, note FROM bounties WHERE repo='aibtc.com' "
                "AND status IN ('found','attempted')"):
            _, fresh = _upsert(c, url.rsplit("/", 1)[-1], "aibtc", "agent", "aibtc", url,
                               funds=0.9, needs=title[:200])
            new += fresh
    # 4) сторонние агенты, прошедшие шлюз с пользой (не пробы и не инъекции)
    if "external_agents" in have:
        for handle, useful in c.execute(
                "SELECT handle, useful FROM external_agents WHERE useful>0 AND handle<>'mtbx_probe'"):
            _, fresh = _upsert(c, handle, "gateway", "agent", "gateway", funds=0.3)
            new += fresh
    c.commit()
    total = c.execute("SELECT COUNT(*) FROM counterparties").fetchone()[0]
    c.close()
    return {"counterparties": total, "new": new}


# ═══════════════════════════════════════════ OBSERVE-3: кто платит прямо сейчас
# x402scan ведёт публичный реестр ПОКУПАТЕЛЕЙ x402: кошельки, число продавцов, объём,
# фасилитаторы (15.09: 18 511 плательщиков за 30 дней; крупнейшие платят сотням
# продавцов через fluxa, coinbase, payAI, openx402, dexter). До кошелька не написать,
# но по нему видно, ГДЕ покупатели ищут — через какие экосистемы, — и с ним сверяется
# каждое поступление. Это и есть «те, кто хочет и пытается платить».
X402SCAN_TRPC = "https://www.x402scan.com/api/trpc/"


def _trpc(proc, inp, timeout=40):
    import urllib.request, urllib.parse
    q = urllib.parse.urlencode({"batch": 1, "input": json.dumps({"0": {"json": inp}})})
    r = urllib.request.urlopen(urllib.request.Request(X402SCAN_TRPC + proc + "?" + q,
                                                      headers={"User-Agent": "P0-dealer/1.0"}), timeout=timeout)
    return json.loads(r.read())[0]["result"]["data"]["json"]


def _due(name, hours):
    """Стратегия исполняется не чаще, чем раз в hours: время — в dealer_strategies.last_at."""
    c = _con()
    r = c.execute("SELECT last_at FROM dealer_strategies WHERE name=?", (name,)).fetchone()
    c.close()
    if not r or not r[0]:
        return True
    try:
        return datetime.now(timezone.utc) - datetime.fromisoformat(r[0]) >= timedelta(hours=hours)
    except ValueError:
        return True


def _touch_strategy(name, note=None, tried=True):
    c = _con()
    c.execute("INSERT INTO dealer_strategies(name,tries,last_at,note) VALUES (?,?,?,?) "
              "ON CONFLICT(name) DO UPDATE SET tries=tries+?, last_at=excluded.last_at, "
              "note=COALESCE(excluded.note, dealer_strategies.note)",
              (name, 1 if tried else 0, now(), note, 1 if tried else 0))
    c.commit(); c.close()


def find_payers(pages=3, timeframe=30):
    """Реестр активных плательщиков x402 → контрагенты со статусом watch (без прямого канала)."""
    from collections import Counter
    buyers = []
    for page in range(pages):
        d = _trpc("public.buyers.all.list", {"pagination": {"page": page, "page_size": 100},
                                            "timeframe": timeframe})
        buyers += d.get("items") or []
        if not d.get("hasNextPage"):
            break
    c = _con()
    new = multi = 0
    fac = Counter()
    for b in buyers:
        sellers = int(b.get("unique_sellers") or 0)
        usd = int(b.get("total_amount") or 0) / 1e6
        facs = str(b.get("facilitator_ids") or "")
        for f in re.findall(r"[A-Za-z0-9_-]+", facs):
            fac[f] += 1
        if sellers < 2:
            continue                     # один продавец — конвейер под одну задачу, не рынок
        multi += 1
        cid, fresh = _upsert(c, b["sender"], "x402scan", "agent", "x402scan_buyers",
                             f"sellers={sellers};fac={facs}", wallet=b["sender"],
                             funds=round(min(1.0, 0.5 + min(0.5, usd / 1000.0)), 3),
                             needs=f"buys x402 services: {sellers} sellers, {b.get('tx_count')} calls/30d via {facs}")
        c.execute("UPDATE counterparties SET status='watch', reliability=0.9, updated_at=? "
                  "WHERE id=? AND status IN ('new','watch')", (now(), cid))
        new += fresh
    c.commit(); c.close()
    top = ", ".join(f"{k}:{v}" for k, v in fac.most_common(6))
    _touch_strategy("payer_mapping", f"плательщиков {len(buyers)}, с ≥2 продавцами {multi}; экосистемы: {top}")
    return {"buyers": len(buyers), "multi_seller": multi, "new": new, "facilitators": top}


# ═══════════════════════════════════════════ OBSERVE-4: индексы и рынки, где ищут покупатели
# Способ попадания в каждый — по факту проверки 15.09, не по вере:
#   x402scan  — публичный tRPC registerFromOrigin, без аккаунта; читает /openapi.json;
#   Bazaar    — только после ОПЛАЧЕННОГО вызова через CDP (владелец запретил платить себе);
#   PayAI     — лента /discovery/resources у фасилитатора (попадают продавцы, считающиеся через него);
#   fluxa AgentMarket, Indexter, agentic.market, x402.rs, taskmarket — вход или свой протокол.
INDEXES = [
    ("x402scan", "https://www.x402scan.com/resources/register", "api: public.resources.registerFromOrigin (без аккаунта; читает /openapi.json)", 0),
    ("bazaar", "https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources", "только после оплаченного вызова через CDP-фасилитатор", 0),
    ("payai_discovery", "https://facilitator.payai.network/discovery/resources", "лента фасилитатора PayAI: попадают продавцы, считающиеся через него", None),
    ("agentic.market", "https://agentic.market/api/markdown", "каталог x402-сервисов «без регистрации» — заполняется обходом", None),
    ("indexter", "https://indexter.cash/providers", "каталог провайдеров экосистемы Dexter", None),
    ("fluxa_market", "https://monetize.fluxapay.xyz/marketplace", "FluxA AgentMarket — вход по аккаунту (list your)", 1),
    ("x402rs_registry", "https://x402.rs/registry", "реестр x402.rs", None),
    ("taskmarket", "https://taskmarket.dev/api/tasks", "эскроу-задачи с бюджетом: CLI + кошелёк-исполнитель + согласие с условиями", 1),
    ("mcp_registry", "https://registry.modelcontextprotocol.io", "опубликован (io.github.mike-lblc/x402-bazaar-rank)", 0),
]
OUR_HOST = SERVICE_URL.split("//", 1)[1]


def _register_x402scan():
    import urllib.request
    body = json.dumps({"0": {"json": {"origin": SERVICE_URL}}}).encode()
    r = urllib.request.urlopen(urllib.request.Request(
        X402SCAN_TRPC + "public.resources.registerFromOrigin?batch=1", data=body,
        headers={"content-type": "application/json", "User-Agent": "P0-dealer/1.0"}), timeout=60)
    d = json.loads(r.read())[0]["result"]["data"]["json"]
    return {k: d.get(k) for k in ("success", "registered", "publicCount", "apiKeyCount", "failed", "skipped", "total", "originId")}


def _in_feed(url, pages=8, limit=100):
    """Есть ли наш хост в ленте обнаружения (Bazaar-подобной): листаем до pages страниц."""
    import urllib.request
    for page in range(pages):
        try:
            r = urllib.request.urlopen(urllib.request.Request(
                f"{url}?limit={limit}&offset={page * limit}", headers={"User-Agent": "P0-dealer/1.0"}), timeout=30)
            raw = r.read().decode("utf-8", "ignore")
        except Exception as e:
            return None, f"{type(e).__name__}"
        if OUR_HOST in raw:
            return True, f"страница {page}"
        if len(raw) < 200 or '"items":[]' in raw.replace(" ", ""):
            break
    return False, f"не найден в {pages} страницах"


def seek_indexes(discover=True):
    """Индексы и рынки: проверка живости, регистрация там, где есть API, поиск новых через GitHub."""
    from agents import bounty
    c = _con()
    stamp = now()
    for key, url, how, needs_acc in INDEXES:
        c.execute("INSERT OR IGNORE INTO channels(key,platform,kind,url,how,needs_account,allows_wallet_address,"
                  "allows_links,alive,discovered_at) VALUES (?,?,?,?,?,?,?,?,NULL,?)",
                  (f"index:{key}", key, "index", url, how, needs_acc, 1, 1, stamp))
    c.commit(); c.close()
    report = {}
    # регистрация по API — идемпотентна, повторяется, чтобы подхватывались изменения openapi
    try:
        reg = _register_x402scan()
        report["x402scan"] = f"registered {reg.get('registered')}/{reg.get('total')}"
        c = _con()
        c.execute("UPDATE channels SET alive=1, evidence=?, checked_at=?, uses=uses+1, results=? WHERE key='index:x402scan'",
                  (json.dumps(reg, ensure_ascii=False)[:300], now(), int(reg.get("registered") or 0)))
        c.commit(); c.close()
    except Exception as e:
        report["x402scan"] = f"ERR {type(e).__name__}"
    # присутствие в лентах обнаружения
    for key, url in (("bazaar", "https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources"),
                     ("payai_discovery", "https://facilitator.payai.network/discovery/resources")):
        present, why = _in_feed(url)
        report[key] = ("в индексе" if present else ("не отвечает: " + why if present is None else "нас нет: " + why))
        c = _con()
        c.execute("UPDATE channels SET alive=?, evidence=?, checked_at=? WHERE key=?",
                  (None if present is None else 1, report[key], now(), f"index:{key}"))
        c.commit(); c.close()
    # живость остальных
    for key, url, how, needs_acc in INDEXES:
        if key in ("x402scan", "bazaar", "payai_discovery", "mcp_registry"):
            continue
        try:
            alive = bounty.platform_alive(url)
        except Exception:
            alive = None
        c = _con()
        c.execute("UPDATE channels SET alive=?, checked_at=? WHERE key=?",
                  (None if alive is None else int(bool(alive)), now(), f"index:{key}"))
        c.commit(); c.close()
        report[key] = "жив" if alive else ("неизвестно" if alive is None else "мёртв")
    # НОВЫЕ индексы — поиском по GitHub: репозитории про x402 с признаками каталога
    found = 0
    if discover:
        import subprocess
        try:
            out = subprocess.run(["gh", "search", "repos", "x402", "--sort", "updated", "--limit", "40",
                                  "--json", "fullName,description,homepage,stargazersCount"],
                                 capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90).stdout
            rows = json.loads(out or "[]")
        except Exception:
            rows = []
        pat = re.compile(r"market|registry|index|scan|director|bazaar|discover|catalog|explorer", re.I)
        c = _con()
        for r in rows:
            desc = f"{r.get('description') or ''} {r.get('homepage') or ''}"
            if not pat.search(desc) or not r.get("homepage"):
                continue
            key = f"index_candidate:{r['fullName']}"
            cur = c.execute("INSERT OR IGNORE INTO channels(key,platform,kind,url,how,needs_account,alive,evidence,discovered_at) "
                            "VALUES (?,?,?,?,?,?,NULL,?,?)",
                            (key, r["fullName"].split("/")[0], "index_candidate", r["homepage"],
                             f"найден поиском GitHub: {(r.get('description') or '')[:120]}", None,
                             f"★{r.get('stargazersCount')}", now()))
            found += cur.rowcount
        c.commit(); c.close()
    _touch_strategy("index_listing", json.dumps(report, ensure_ascii=False)[:400])
    report["new_candidates"] = found
    return report


# ═══════════════════════════════════════════ OBSERVE-5: эскроу-задачи с бюджетом
def import_taskmarket(limit=40):
    """Открытые задачи taskmarket.dev (эскроу USDC на Base) → очередь наград + сигнал о нашем классе.

    Исполнение требует кошелька-исполнителя Taskmarket (CLI, keystore), а первая запись
    на площадке — принятия условий: и то и другое — решение владельца, о нём эскалируется
    один раз. До этого задачи видны в очереди и в отчётах как измеренный спрос.
    """
    import urllib.request
    from agents import bounty
    try:
        r = urllib.request.urlopen(urllib.request.Request("https://taskmarket.dev/api/tasks",
                                                          headers={"User-Agent": "P0-dealer/1.0"}), timeout=30)
        tasks = (json.loads(r.read()).get("tasks") or [])[:limit]
    except Exception as e:
        return {"error": type(e).__name__}
    rows, ours = [], 0
    for t in tasks:
        if t.get("status") != "open":
            continue
        usd = int(t.get("reward") or 0) / 1e6
        desc = str(t.get("description") or "")
        title = desc.splitlines()[0][:120] if desc else t.get("referenceCode", "")
        url = f"https://taskmarket.dev/tasks/{t.get('id')}"
        rows.append({"url": url, "title": f"[taskmarket] {title}", "amount_usd": round(usd, 2),
                     "participants": int(t.get("submissionCount") or 0) + int(t.get("pitchCount") or 0),
                     "note": (f"эскроу {str(t.get('escrowTxHash') or '')[:12]}…; заказчик {str(t.get('requester') or '')[:10]}; "
                              f"срок {str(t.get('expiryTime') or '')[:10]}; режим {t.get('mode')}; "
                              f"исполнение — CLI taskmarket с кошельком-исполнителем (решение владельца)"),
                     "declared_proof": f"эскроу на Base: tx {t.get('escrowTxHash')}"})
        low = desc.lower()
        if any(k in low for k in ("document", "reference", "readme", "guide", "write", "summar", "compact",
                                  "tool", "script", "csv", "dataset", "translate")) and usd >= 0.5:
            ours += 1
            bounty._flag_once("taskmarket", str(t.get("id")),
                              f"Taskmarket: эскроу-задача НАШЕГО класса «{title[:70]}» за {usd:.2f} USDC "
                              f"(заявок {rows[-1]['participants']})",
                              {"url": url, "usd": usd, "описание": desc[:1500],
                               "как сдать": "нужен кошелёк-исполнитель Taskmarket (taskmarket init) и согласие с условиями "
                                            "(taskmarket legal) — решение владельца; затем claim/submit по skill.md"})
    new = seen = 0
    if rows:
        new, seen = bounty.ingest(rows, "taskmarket.dev",
                                  note="Taskmarket: эскроу-задачи агентов; выплата USDC на Base кошельку-исполнителю")
    if ours:
        bounty._flag_once("taskmarket", "enable",
                          "Taskmarket.dev — рынок эскроу-задач для агентов; чтобы брать их, нужен кошелёк-исполнитель "
                          "(taskmarket init) и согласие с условиями — решение владельца",
                          {"skill": "https://taskmarket.dev/skill.md", "open_tasks": len(rows), "our_class": ours})
    _touch_strategy("escrow_tasks", f"открытых {len(rows)}, нашего класса {ours}, новых в очереди {new}")
    return {"open": len(rows), "our_class": ours, "new": new, "seen": seen}


# ═══════════════════════════════════════════ MODEL + PRIORITIZE
def _priority(funds, response_p, cooperation, reliability, wallet_known, contacts, replies,
              expected_tx_h):
    """PRIORITY = P(перевода) × P(подтверждения) × доступность ÷ ожидаемое время (сутки)."""
    p_transfer = min(1.0, 0.05 + 0.5 * funds + 0.3 * response_p + 0.15 * cooperation)
    p_confirm = 0.95 if wallet_known else 0.6 + 0.3 * reliability
    availability = 1.0 if replies > 0 else (0.7 if contacts == 0 else 0.35)
    days = max(0.5, (expected_tx_h or 96) / 24.0)
    return round(p_transfer * p_confirm * availability / days, 4)


def model():
    """MODEL: пересчитать поля контрагентов по истории обращений и ответов, затем приоритет."""
    c = _con()
    # история обращений продавца и закрывающего — это и есть наши факты об ответах
    have = _tables(c)
    contacted = {}
    if "outreach" in have:
        for ch, dom, url, sent_at in c.execute("SELECT channel, domain, url, sent_at FROM outreach"):
            contacted[("github", ch)] = (url, sent_at)
    replied = {}
    if "outreach_replies" in have:
        for dom, url, author, body in c.execute(
                "SELECT domain, url, author, body FROM outreach_replies"):
            replied.setdefault(url, []).append((author, (body or "")[:200]))
    updated = 0
    for cid, handle, platform, funds, coop, rel, wallet, contacts, replies, exp_h, status in c.execute(
            "SELECT id, handle, platform, funds_signal, cooperation, reliability, wallet, contacts, "
            "replies, expected_tx_h, status FROM counterparties").fetchall():
        contacts = contacts or 0
        replies = replies or 0
        st = status
        key = (platform, handle)
        if key in contacted:
            url, sent_at = contacted[key]
            contacts = max(contacts, 1)
            st = "contacted" if st in ("new", "contacted") else st
            if replied.get(url):
                replies = max(replies, len(replied[url]))
                st = "replied" if st in ("new", "contacted", "replied") else st
        prior = PLATFORM_PRIOR.get(platform, PLATFORM_PRIOR["gateway"])["response_p"]
        response_p = round((replies + prior) / (contacts + 1), 3)
        pr = _priority(funds or 0, response_p, coop or 0.5, rel or 0.5, bool(wallet),
                       contacts, replies, exp_h)
        c.execute("UPDATE counterparties SET contacts=?, replies=?, response_p=?, priority=?, "
                  "status=?, updated_at=? WHERE id=?",
                  (contacts, replies, response_p, pr, st, now(), cid))
        updated += 1
    c.commit(); c.close()
    return {"modelled": updated}


def top_targets(limit=5, only_new=False):
    c = _con()
    sql = ("SELECT id, handle, platform, kind, priority, status, funds_signal, needs, source, "
           "source_ref FROM counterparties WHERE status<>'dead' ")
    if only_new:
        sql += "AND status='new' "
    rows = c.execute(sql + "ORDER BY priority DESC, funds_signal DESC LIMIT ?", (limit,)).fetchall()
    c.close()
    keys = ("id", "handle", "platform", "kind", "priority", "status", "funds", "needs", "source", "ref")
    return [dict(zip(keys, r)) for r in rows]


# ═══════════════════════════════════════════ ACT
def _offer_for(needs):
    """Конкретная ценность под его слова: только то, что мы умеем исполнить и доказать."""
    t = (needs or "").lower()
    if any(k in t for k in ("x402", "endpoint", "api", "seller", "catalog", "market", "rank", "payers")):
        return ("a ranked read of the live x402 market for your capability: every indexed service "
                "with 30-day calls, paying-wallet bounds and price band, with a receipt naming the "
                "snapshot hash and scoring revision", "one /search query", "0.01", "USDC", "base")
    if any(k in t for k in ("doc", "readme", "guide", "quickstart", "translat", "russian", "reference")):
        return ("a command reference or quickstart written from your CLI's own source and verified "
                "line by line (merged example: omi-cli quickstart)", "one documentation page",
                "5", "USDC", "base")
    if any(k in t for k in ("audit", "402", "timeout", "broken", "bug", "test")):
        return ("an x402 compatibility audit of your endpoints: what your 402 actually returns, "
                "timeouts, broken paths and where you sit in the catalog", "one written audit",
                "5", "USDC", "base")
    return ("a ranked read of the live x402 market for any capability you name, with a receipt "
            "naming the snapshot hash and scoring revision", "one /search query", "0.01", "USDC", "base")


def compose(cp, channel, snapshot_hash=None):
    """Раздел VIII: шесть обязательных элементов в каждом обращении.

    1 — почему именно он; 2 — конкретная ценность; 3 — точная, выполнимая просьба;
    4 — куда платить (адрес, если канал его допускает, иначе ссылка, чей ответ 402
    несёт адрес); 5 — срок ответа; 6 — как проверить нашу часть.
    Только факты: числа берутся из базы, обещания не выдумываются.
    """
    value, unit, amount, cur, net = _offer_for(cp.get("needs"))
    why = {
        "moltbook_demand": f"You wrote «{(cp.get('needs') or '')[:70]}» — you pay for results and take "
                           f"paid work, so a cheap, verifiable data call may be useful to you.",
        "moltbook_inbound": "You replied under our post, so you already know what we measure.",
        "leads": "Your endpoints are in our x402 index and you are receiving payments there, "
                 "so a market read is directly useful to you.",
        "bounties": "Your project publishes rewards for finished work; we deliver finished work.",
        "aibtc": "Your bounty is on the AIBTC board where agents pay agents in sBTC.",
    }.get(cp.get("source"), "You are active where agents pay for results.")
    addr_ok = bool(channel.get("allows_wallet_address"))
    if addr_ok and net == "base":
        pay_line = (f"Pay to {payment.OWNER_DESTINATIONS['evm']} (USDC on Base) — or simply call "
                    f"{SERVICE_URL}/search?q=<capability>: the 402 response carries the same address "
                    f"and settles the payment for you.")
    elif net == "stacks":
        pay_line = (f"Pay in sBTC to the registered agent wallet "
                    f"{payment.OWNER_DESTINATIONS.get('stx', '')} on Stacks.")
    else:
        pay_line = (f"Call {SERVICE_URL}/search?q=<capability>: the 402 response carries the "
                    f"receiving address and settles the payment; nothing else to set up.")
    verify = (f"Verify our side for free first: {SERVICE_URL}/health shows the snapshot hash"
              + (f" ({snapshot_hash})" if snapshot_hash else "")
              + f" and {SERVICE_URL}/sample returns three ranked results with the same receipt.")
    lines = [
        why,
        "",
        f"What you get: {value}.",
        f"What we ask: {amount} {cur} for {unit}. Nothing more, no retainer, no account to create.",
        pay_line,
        f"If it is useful, reply within {RESPONSE_WINDOW_H} hours; if not, no follow-up from us — "
        f"we write once.",
        verify,
    ]
    return "\n".join(lines), {"amount": amount, "currency": cur, "network": net}


def _channel(key):
    c = _con()
    r = c.execute("SELECT key, platform, kind, url, allows_wallet_address, allows_links, alive "
                  "FROM channels WHERE key=?", (key,)).fetchone()
    c.close()
    if not r:
        return {"key": key, "platform": "", "allows_wallet_address": 0, "allows_links": 0}
    return dict(zip(("key", "platform", "kind", "url", "allows_wallet_address", "allows_links", "alive"), r))


def _log_action(kind, payload, result, dry_run):
    c = connect()
    c.execute("INSERT INTO actions(kind,action_class,dry_run,payload,result,created_at) "
              "VALUES (?,?,?,?,?,?)", (kind, "YELLOW", int(bool(dry_run)),
                                       json.dumps(payload, ensure_ascii=False)[:1500],
                                       str(result)[:500], now()))
    c.commit(); c.close()


def act(dry_run=False, limit=MAX_MESSAGES_PER_CYCLE):
    """ACT: обращения к лучшим контрагентам там, где никто другой не действует.

    Агенты Moltbook с деньгами — единственная группа без своего агента: продавец
    пишет продавцам x402 через GitHub, мастеровой берёт награды, канал ведёт
    посты. Дилер отвечает под ИХ постом: один раз на контрагента, в пределах
    CAPS, с шестью элементами протокола. Остальные группы делегируются тому,
    кто ими уже занимается, и это записывается как попытка с исходом «delegated».
    """
    sent, delegated, skipped = [], [], []
    snap = None
    try:
        snap = json.loads((ROOT / "worker" / "snapshot.json").read_text(encoding="utf-8")).get("sha256")
    except Exception:
        pass
    for cp in top_targets(limit=12, only_new=True):
        if len(sent) >= limit:
            break
        if cp["platform"] == "moltbook" and cp.get("ref"):
            if not dry_run:
                try:
                    guard.check_action("dealer_message", "YELLOW")
                except Exception as e:
                    skipped.append(f"{cp['handle']}: {type(e).__name__}")
                    break
            # сабмолт поста — политика адресов берётся по нему
            c = _con()
            sub = c.execute("SELECT submolt FROM moltbook_demand WHERE post_id=?",
                            (cp["ref"],)).fetchone()
            c.close()
            sub = sub[0] if sub else "general"
            ch = _channel(f"moltbook:{sub}")
            text, ask = compose(cp, ch, snap)
            if dry_run:
                sent.append(f"{cp['handle']}@{sub} (dry)")
                continue
            from core import moltbook as mb
            res = None
            try:
                res = mb.add_comment("dealer", cp["ref"], text, submolt=sub, allow_own_links=True)
            except Exception as e:
                # ОБХОД, А НЕ ОСТАНОВКА. Отказ гварда на ссылки → вариант без ссылок (пути
                # вместо адресов); любой другой отказ → канал помечается, событие уходит
                # управляющему каналами, контрагент не теряется.
                if "link" in str(e).lower():
                    bare = re.sub(r"https?://[^\s)]+", lambda m: m.group(0).split("workers.dev", 1)[-1] or "/", text)
                    try:
                        res = mb.add_comment("dealer", cp["ref"], bare, submolt=sub)
                        _touch_strategy("moltbook_reply", "обход: вариант без ссылок принят")
                    except Exception as e2:
                        e = e2
                if res is None:
                    c = _con()
                    c.execute("UPDATE counterparties SET status=?, updated_at=? WHERE id=?",
                              (f"blocked:{type(e).__name__}"[:40], now(), cp["id"]))
                    c.execute("UPDATE channels SET results=results-1, evidence=?, checked_at=? WHERE key=?",
                              (f"отказ: {type(e).__name__}: {str(e)[:120]}", now(), ch["key"]))
                    c.commit(); c.close()
                    try:
                        from core import events
                        events.publish("channel_unavailable", {"channel": ch["key"], "why": str(e)[:200],
                                                                "counterparty": cp["handle"]}, source="dealer")
                    except Exception:
                        pass
                    skipped.append(f"{cp['handle']}: {type(e).__name__}: {str(e)[:80]}")
                    continue
            ref = res.get("external_id")
            published = bool(res.get("published")) or res.get("state") == "CONFIRMED"
            _log_action("dealer_message", {"to": cp["handle"], "post": cp["ref"], "ask": ask},
                        f"{res.get('state')} {ref}", False)
            c = _con()
            c.execute("INSERT INTO dealer_attempts(counterparty_id,channel_key,message_ref,ask_amount,"
                      "ask_currency,ask_network,deadline_at,outcome,created_at,updated_at) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (cp["id"], ch["key"], ref, ask["amount"], ask["currency"], ask["network"],
                       (datetime.now(timezone.utc) + timedelta(hours=RESPONSE_WINDOW_H)).isoformat(),
                       "sent" if published else "pending", now(), now()))
            c.execute("UPDATE counterparties SET status='contacted', contacts=contacts+1, "
                      "last_contact_at=?, updated_at=? WHERE id=?", (now(), now(), cp["id"]))
            c.execute("INSERT INTO dealer_strategies(name,tries,last_at) VALUES ('moltbook_reply',1,?) "
                      "ON CONFLICT(name) DO UPDATE SET tries=tries+1, last_at=excluded.last_at", (now(),))
            c.commit(); c.close()
            sent.append(f"{cp['handle']}@{sub}:{str(ref)[:8]}")
        else:
            owner = {"leads": "salesman", "bounties": "craftsman", "aibtc": "craftsman",
                     "gateway": "channel_manager"}.get(cp.get("source"), "salesman")
            c = _con()
            c.execute("UPDATE counterparties SET status=?, updated_at=? WHERE id=?",
                      (f"delegated:{owner}", now(), cp["id"]))
            c.execute("INSERT INTO dealer_attempts(counterparty_id,channel_key,outcome,created_at,"
                      "updated_at) VALUES (?,?,?,?,?)",
                      (cp["id"], cp["platform"], "delegated", now(), now()))
            c.commit(); c.close()
            delegated.append(f"{cp['handle']}→{owner}")
    return {"sent": sent, "delegated": delegated, "skipped": skipped}


# ═══════════════════════════════════════════ VERIFY
def _sender_kind(from_party):
    if not from_party:
        return "unknown", None
    c = _con()
    r = c.execute("SELECT id, kind FROM counterparties WHERE wallet=? COLLATE NOCASE",
                  (from_party,)).fetchone()
    c.close()
    if r:
        return r[1], r[0]
    return "unknown", None


def verify():
    """VERIFY: каждое поступление — по хешу, с отправителем, сопоставленным с контрагентами.

    Условие успеха (раздел II, в редакции владельца): подтверждённый перевод от
    ЛЮБОГО постороннего на наш адрес, положительная сумма, окончательность по
    сети. Перевод от самого владельца отвергается ещё при записи поступления.
    """
    c = _con()
    rows = []
    if "payment_receipts" in _tables(c):
        rows = c.execute("SELECT proof, from_party, network, currency, net FROM payment_receipts "
                         "WHERE proof_kind='tx_hash'").fetchall()
    new = 0
    for proof, frm, net, cur, amount in rows:
        if c.execute("SELECT 1 FROM dealer_receipts WHERE proof=?", (proof,)).fetchone():
            continue
        kind, cid = _sender_kind(frm)
        c.execute("INSERT INTO dealer_receipts(proof,from_party,network,currency,amount,sender_kind,"
                  "counterparty_id,seen_at) VALUES (?,?,?,?,?,?,?,?)",
                  (proof, frm, net, cur, amount, kind, cid, now()))
        if cid:
            c.execute("UPDATE counterparties SET status='paid', updated_at=? WHERE id=?", (now(), cid))
            c.execute("UPDATE dealer_attempts SET outcome='paid', updated_at=? WHERE counterparty_id=?",
                      (now(), cid))
        new += 1
    c.commit()
    total = c.execute("SELECT COUNT(*) FROM dealer_receipts").fetchone()[0]
    c.close()
    success = total > 0
    if new and success:
        bus.broadcast("dealer", f"ПЛАТЁЖ ПОДТВЕРЖДЁН ХЕШЕМ: новых поступлений {new}, всего {total}. "
                                f"Условие миссии выполнено — дальше следующий плательщик.")
        try:
            from agents import council
            council.escalate("dealer", "SUCCESS: подтверждённый перевод от постороннего — сообщить владельцу",
                             json.dumps({"new": new, "total": total}, ensure_ascii=False))
        except Exception:
            pass
    return {"receipts": total, "new": new, "success": success}


# ═══════════════════════════════════════════ LEARN
_OBJECTION = re.compile(r"\b(no thanks|not interested|spam|wrong|too expensive|don't need|do not need|"
                        r"can't pay|cannot pay|stop)\b", re.I)


def learn():
    """LEARN: ответы → вероятность ответа и возражения; попытки → исходы; стратегии → доли успеха."""
    c = _con()
    learned = 0
    # ответы под нашими обращениями в Moltbook — по попыткам дилера
    if "moltbook_inbound" in _tables(c):
        for aid, cid, ref in c.execute("SELECT id, counterparty_id, message_ref FROM dealer_attempts "
                                       "WHERE outcome IN ('sent','pending') AND message_ref IS NOT NULL"):
            r = c.execute("SELECT body FROM moltbook_inbound WHERE post_id IN "
                          "(SELECT source_ref FROM counterparties WHERE id=?) "
                          "AND created_at > (SELECT created_at FROM dealer_attempts WHERE id=?) "
                          "ORDER BY created_at DESC LIMIT 1", (cid, aid)).fetchone()
            if not r:
                continue
            body = r[0] or ""
            obj = _OBJECTION.search(body)
            c.execute("UPDATE dealer_attempts SET outcome=?, objection=?, updated_at=? WHERE id=?",
                      ("refused" if obj else "replied", body[:200] if obj else None, now(), aid))
            c.execute("UPDATE counterparties SET replies=replies+1, status=?, updated_at=? WHERE id=?",
                      ("replied", now(), cid))
            if obj:
                c.execute("UPDATE counterparties SET objections=?, cooperation=MAX(0.1, cooperation-0.2) "
                          "WHERE id=?", (json.dumps([body[:200]], ensure_ascii=False), cid))
            learned += 1
    # просроченные попытки без ответа
    c.execute("UPDATE dealer_attempts SET outcome='expired', updated_at=? "
              "WHERE outcome IN ('sent','pending') AND deadline_at IS NOT NULL AND deadline_at < ?",
              (now(), now()))
    # доли успеха стратегий
    for name, in c.execute("SELECT name FROM dealer_strategies").fetchall():
        succ = c.execute("SELECT COUNT(*) FROM dealer_attempts WHERE outcome='paid' AND channel_key LIKE ?",
                         (f"{name.split('_')[0]}%",)).fetchone()[0]
        c.execute("UPDATE dealer_strategies SET successes=? WHERE name=?", (succ, name))
    c.commit(); c.close()
    return {"learned": learned}


# ═══════════════════════════════════════════ STATE (раздел IX)
def state(write=True):
    c = _con()
    money = {}
    try:
        money = payment.state_of_money()
    except Exception:
        pass
    verified = [dict(zip(("proof", "from", "network", "currency", "amount", "sender"), r)) for r in
                c.execute("SELECT proof, from_party, network, currency, amount, sender_kind "
                          "FROM dealer_receipts ORDER BY seen_at DESC LIMIT 10")]
    active = [dict(zip(("id", "objective", "state"), r)) for r in
              c.execute("SELECT id, substr(objective,1,60), state FROM tasks WHERE kind='deal' "
                        "AND state IN ('CONTACTED','REPLIED','NEGOTIATING','AGREED','WORKING','QA',"
                        "'DELIVERED','PAYMENT_REQUESTED','PAYMENT_PENDING') ORDER BY id DESC LIMIT 12")]
    strategies = [dict(zip(("name", "tries", "successes"), r)) for r in
                  c.execute("SELECT name, tries, successes FROM dealer_strategies")]
    failed = [dict(zip(("channel", "objection"), r)) for r in
              c.execute("SELECT channel_key, objection FROM dealer_attempts WHERE outcome IN "
                        "('refused','expired') ORDER BY updated_at DESC LIMIT 8")]
    channels = c.execute("SELECT COUNT(*), SUM(CASE WHEN alive=1 THEN 1 ELSE 0 END) FROM channels").fetchone()
    cps = c.execute("SELECT COUNT(*), SUM(CASE WHEN status='new' THEN 1 ELSE 0 END) FROM counterparties").fetchone()
    top = top_targets(5)
    # оценка вероятности следующего платежа: сумма приоритетов лучших целей, срезанная единицей
    est = round(min(0.95, sum(t["priority"] for t in top) * 0.5 + (0.3 if verified else 0)), 3)
    st = {
        "at": now(),
        "wallet_balance": money,
        "verified_transactions": verified,
        "active_negotiations": active,
        "highest_probability_targets": [{k: t[k] for k in ("handle", "platform", "kind", "priority", "status")}
                                        for t in top],
        "current_strategies": strategies,
        "failed_approaches": failed,
        "next_actions": [f"contact {t['handle']}@{t['platform']}" for t in top if t["status"] == "new"][:3]
                        or ["watch replies and receipts"],
        "channels": {"total": channels[0], "alive": channels[1] or 0},
        "counterparties": {"total": cps[0], "new": cps[1] or 0},
        "estimated_success_probability": est,
        "success": bool(verified),
    }
    if write:
        c.execute("INSERT INTO dealer_state(at,state) VALUES (?,?)",
                  (st["at"], json.dumps(st, ensure_ascii=False)))
        c.execute("DELETE FROM dealer_state WHERE id NOT IN (SELECT id FROM dealer_state ORDER BY id DESC LIMIT 200)")
        c.commit()
    c.close()
    return st


def last_state():
    c = _con()
    r = c.execute("SELECT state FROM dealer_state ORDER BY id DESC LIMIT 1").fetchone()
    c.close()
    return json.loads(r[0]) if r else None


# ═══════════════════════════════════════════ ЦИКЛ
def cycle(dry_run=False):
    guard.check_action("research", "GREEN")
    w = weave()
    p = probe_channels()
    extra = {}
    if _due("payer_mapping", 6):
        try: extra["payers"] = find_payers()
        except Exception as e: extra["payers"] = f"ERR {type(e).__name__}"
    if _due("index_listing", 6):
        try: extra["indexes"] = seek_indexes()
        except Exception as e: extra["indexes"] = f"ERR {type(e).__name__}"
    if _due("escrow_tasks", 2):
        try: extra["taskmarket"] = import_taskmarket()
        except Exception as e: extra["taskmarket"] = f"ERR {type(e).__name__}"
    s = survey()
    m = model()
    a = act(dry_run=dry_run)
    v = verify()
    l = learn()
    st = state()
    return (f"channels {w['channels']} (alive {w['alive']}, probed {len(p)}); "
            f"counterparties {s['counterparties']} (+{s['new']}); "
            f"sent {len(a['sent'])} delegated {len(a['delegated'])} skipped {len(a['skipped'])}; "
            f"receipts {v['receipts']} (+{v['new']}); learned {l['learned']}; "
            f"p(next payment)≈{st['estimated_success_probability']}"
            + (f"; web: {json.dumps(extra, ensure_ascii=False)[:220]}" if extra else "")
            + ("; SUCCESS" if v["success"] else ""))


def report():
    st = last_state() or state()
    top = ", ".join(f"{t['handle']}@{t['platform']}({t['priority']})"
                    for t in st.get("highest_probability_targets", [])[:5]) or "-"
    return (f"дилер: каналов {st['channels']['total']} (живых {st['channels']['alive']}), "
            f"контрагентов {st['counterparties']['total']} (новых {st['counterparties']['new']}), "
            f"поступлений с хешем {len(st['verified_transactions'])}, "
            f"лучшие цели: {top}; p(следующий платёж)≈{st['estimated_success_probability']}")


if __name__ == "__main__":
    print(cycle(dry_run="--dry" in sys.argv))
    print(json.dumps(last_state(), ensure_ascii=False, indent=1)[:3000])
