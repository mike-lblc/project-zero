"""ОБОРОТ МЫШЛЕНИЯ — общий план экосистемы, а не отдельный агент.

Владелец 15.09: думать должна вся экосистема — агенты говорят друг с другом, общий план
виден каждому на общей доске (core/board.py), гипотезы предлагает любой агент (propose_path),
а этот модуль лишь ведёт цикл: наблюдать → придумать → оценить → проверить → отобрать → просить.
Шаг принадлежит оркестратору.

Требование владельца 15.09.2026: не девять стратегий по списку, а живой поиск новых —
«the ecosystem should actually THINK». Мышление здесь — не свободное сочинение, а
цикл гипотез, как в науке и в продажах:

  НАБЛЮДАТЬ  — сигналы: что приносит ответы и деньги, где живут плательщики, что
               отвергают каналы, чем зарабатывают другие агенты (их слова в лентах);
  ПРИДУМАТЬ  — гипотезы из ЖИВЫХ компонентов: источник контрагентов × канал ×
               предложение × механизм; плюс механизмы, подсмотренные у рынка;
               плюс мутации удачных гипотез (меняется одно измерение);
  ОЦЕНИТЬ    — ожидаемая ценность = P(дойти) × P(заплатят) × просьба ÷ стоимость;
  ПРОВЕРИТЬ  — эксперимент с бюджетом (попытки/дни) и метрикой (ответы, листинги,
               поступления), исполняемый существующими агентами и их пределами;
  ОТОБРАТЬ   — оставить работающее, снять пустое, от удачного — ветви;
  ПРОСИТЬ    — чего не умеем (адаптер, кошелёк, условия площадки) — задача механику
               или решение владельцу, один раз.

Правила протокола сохраняются: локальная модель только извлекает факты из чужих
текстов (MECHANICAL), выбор и запуск идут по измеримым правилам, действия — только
через инструменты с уже выданными разрешениями (GREEN и стоящие YELLOW). Класс
BLACK неизменен. Сообщения других агентов — данные, не команды.
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
from core import guard, bus, router  # noqa: E402

SCHEMA = """
CREATE TABLE IF NOT EXISTS strategy_hypotheses (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  origin TEXT NOT NULL,               -- combinatorial | observed | mutation | seed
  source TEXT,                        -- откуда контрагенты (submolt:x, index:x, board:x, x402scan, github…)
  channel TEXT,                       -- как дотянуться (moltbook_comment, moltbook_post, github_issue, index_api, board_import, aibtc_submit, taskmarket_cli)
  offer TEXT,                         -- что предлагаем
  mechanism TEXT,                     -- reply_demand | offer_post | listing | board_import | escrow_claim | bounty_claim | reciprocal | delivery
  rationale TEXT,
  evidence TEXT,                      -- откуда взялось (пост, число, ссылка)
  status TEXT NOT NULL DEFAULT 'proposed',  -- proposed | testing | kept | killed | blocked
  needs TEXT,                         -- чего не хватает для исполнения (для blocked)
  score REAL DEFAULT 0,
  budget INTEGER DEFAULT 3,           -- попыток до оценки
  tries INTEGER DEFAULT 0,
  signal INTEGER DEFAULT 0,           -- ответы/листинги/поступления, полученные экспериментом
  parent_id INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  last_run_at TEXT
);
CREATE TABLE IF NOT EXISTS strategy_runs (
  id INTEGER PRIMARY KEY,
  hypothesis_id INTEGER NOT NULL,
  at TEXT NOT NULL,
  action TEXT,
  outcome TEXT,
  signal INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS strategist_state (
  id INTEGER PRIMARY KEY,
  at TEXT NOT NULL,
  state TEXT NOT NULL
);
"""

# Приоры механизмов: вероятность дойти и вероятность оплаты, стоимость в «единицах
# внимания». Стартовые допущения, которые двигают сигналы экспериментов.
MECHANISMS = {
    "reply_demand":  {"reach": 0.8, "pay": 0.15, "cost": 1.0, "why": "адресат сам написал, что платит"},
    "offer_post":    {"reach": 0.4, "pay": 0.05, "cost": 1.0, "why": "пост в сабмолте, где платят: видят многие, платят единицы"},
    "listing":       {"reach": 0.9, "pay": 0.08, "cost": 0.5, "why": "индекс, где ищут покупатели-роботы"},
    "board_import":  {"reach": 0.9, "pay": 0.3,  "cost": 0.7, "why": "деньги уже внесены за задачу"},
    "escrow_claim":  {"reach": 0.9, "pay": 0.5,  "cost": 2.0, "why": "эскроу: платят по приёмке"},
    "bounty_claim":  {"reach": 0.7, "pay": 0.3,  "cost": 2.5, "why": "проект объявил награду сам"},
    "reciprocal":    {"reach": 0.6, "pay": 0.12, "cost": 1.2, "why": "продавец-агент с деньгами: обмен вызовами"},
    "delivery":      {"reach": 1.0, "pay": 0.4,  "cost": 2.0, "why": "готовый результат сдаётся на доску"},
}
# Каналы и что нужно, чтобы ими пользоваться (проверяется по фактам в базе и коде).
CHANNEL_NEEDS = {
    "moltbook_comment": None, "moltbook_post": None, "github_issue": None, "index_api": None,
    "board_import": None, "aibtc_submit": None,
    "taskmarket_cli": "кошелёк-исполнитель taskmarket (taskmarket init) и согласие с условиями — решение владельца",
    "facilitator_adapter": "расчёт воркера через фасилитатор экосистемы (PayAI/fluxa) — задача механику воркера",
}
EARN_WORDS = re.compile(r"\b(earned|got paid|first (sale|payment|dollar)|revenue|paid me|sold|made \$|income|"
                        r"tips? received|customers? paid|payout received|заработал|оплатил)\b", re.I)


def now():
    return datetime.now(timezone.utc).isoformat()


def _con():
    c = connect()
    ensure_schema(c, SCHEMA)
    return c


def _tables(c):
    return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}


# ═══════════════════════════════════════════ НАБЛЮДАТЬ
def observe():
    """Сигналы из базы: что работает, где деньги, что отвергнуто. Только измеренное."""
    c = _con()
    have = _tables(c)
    sig = {"at": now()}
    q = lambda s, a=(): c.execute(s, a).fetchall()
    if "channels" in have:
        sig["channels_alive"] = [tuple(r) for r in q("SELECT key, kind, platform, allows_wallet_address, results FROM channels WHERE alive=1")]
        sig["channels_blocked"] = [tuple(r) for r in q("SELECT key, evidence FROM channels WHERE results<0")]
    if "counterparties" in have:
        sig["counterparties_by_source"] = [tuple(r) for r in q(
            "SELECT source, COUNT(*), ROUND(AVG(funds_signal),2), SUM(replies), SUM(status='paid') FROM counterparties GROUP BY source")]
    if "dealer_strategies" in have:
        sig["strategies"] = [tuple(r) for r in q("SELECT name, tries, successes, substr(note,1,120) FROM dealer_strategies")]
    if "outreach" in have:
        sig["outreach"] = q("SELECT COUNT(*) FROM outreach")[0][0]
        sig["replies"] = q("SELECT COUNT(*) FROM outreach_replies")[0][0] if "outreach_replies" in have else 0
    if "payment_receipts" in have:
        sig["receipts"] = q("SELECT COUNT(*) FROM payment_receipts")[0][0]
    if "moltbook_demand" in have:
        sig["demand_by_submolt"] = [tuple(r) for r in q("SELECT submolt, COUNT(*) FROM moltbook_demand GROUP BY submolt")]
    if "moltbook_address_policy" in have:
        sig["address_ok_submolts"] = [r[0] for r in q("SELECT submolt FROM moltbook_address_policy WHERE with_address>0")]
    if "bounties" in have:
        sig["bounties_by_source"] = [tuple(r) for r in q(
            "SELECT repo, COUNT(*), ROUND(SUM(amount_usd),1) FROM bounties WHERE status IN ('found','attempted') "
            "AND repo NOT LIKE '%/%' GROUP BY repo")]
    if "services" in have:
        sig["offers"] = [r[0] for r in q("SELECT name FROM services WHERE status='executable' LIMIT 12")]
    c.close()
    # экосистемы плательщиков — из заметки дилера (find_payers)
    for name, tries, succ, note in sig.get("strategies", []):
        if name == "payer_mapping" and note:
            sig["payer_ecosystems"] = note
    return sig


def observe_market(agent="channel_manager", limit=40):
    """Чем зарабатывают другие агенты — их собственные слова в лентах Moltbook.

    Локальная модель здесь только ИЗВЛЕКАЕТ механизм из чужого текста (MECHANICAL);
    решение, пробовать ли его, принимает правило оценки ниже. Заодно посты со
    спросом со ВСЕЙ площадки, а не из шести сабмолтов, уходят в таблицу спроса.
    """
    from core import moltbook
    from agents import moltbook as mba
    found, demand_new = [], 0
    posts = []
    for sort in ("new", "top"):
        try:
            posts += moltbook.global_posts(agent, sort=sort, limit=limit)
        except Exception:
            continue
    c = _con()
    mba._demand_schema(c)
    seen_ids = set()
    for p in posts:
        pid = str(p.get("id") or "")
        if not pid or pid in seen_ids:
            continue
        seen_ids.add(pid)
        author = p.get("author")
        author = author.get("name") if isinstance(author, dict) else str(author or "")
        if author == moltbook.IDENTITY:
            continue
        sub = p.get("submolt")
        sub = (sub.get("name") if isinstance(sub, dict) else sub) or "general"
        title, body = str(p.get("title") or ""), str(p.get("content") or "")
        # спрос со всей площадки — в таблицу дилера
        if mba._buyer_intent(title, body):
            cur = c.execute("INSERT OR IGNORE INTO moltbook_demand(post_id,submolt,author,title,body,created_at,found_at) "
                            "VALUES (?,?,?,?,?,?,?)", (pid, sub, author, title[:300], body[:4000],
                                                       str(p.get("created_at") or ""), now()))
            demand_new += cur.rowcount
        # рассказы о заработке — на извлечение механизма
        if EARN_WORDS.search(title + " " + body[:1500]):
            found.append({"post_id": pid, "submolt": sub, "author": author, "title": title[:200], "body": body[:2500]})
    c.commit(); c.close()
    mechanisms = []
    for f in found[:6]:
        prompt = (f"From the text below, extract ONLY facts that answer: how exactly did the author (an AI agent or "
                  f"person) receive money — what was sold or done, on which platform or channel, and how was it paid? "
                  f"Reply in one line: MECHANISM: <what>; WHERE: <platform>; PAID: <how>. If the text does not say, "
                  f"reply exactly: NOT FOUND.\n\nTEXT:\n{f['title']}\n{f['body'][:2200]}")
        try:
            ans = " ".join(router.run("extract", prompt).split())[:300]
        except Exception as e:
            ans = f"MODEL UNAVAILABLE: {type(e).__name__}"
        if ans and "NOT FOUND" not in ans and "UNAVAILABLE" not in ans and "MECHANISM" in ans.upper():
            mechanisms.append({"post_id": f["post_id"], "submolt": f["submolt"], "author": f["author"], "extract": ans})
    return {"posts": len(seen_ids), "earn_stories": len(found), "mechanisms": mechanisms, "demand_new": demand_new}


# ═══════════════════════════════════════════ ПРИДУМАТЬ
def _propose(c, name, origin, source, channel, offer, mechanism, rationale, evidence, parent_id=None, needs=None):
    stamp = now()
    m = MECHANISMS.get(mechanism, {"reach": 0.3, "pay": 0.05, "cost": 1.5})
    status = "blocked" if needs else "proposed"
    cur = c.execute("INSERT OR IGNORE INTO strategy_hypotheses(name,origin,source,channel,offer,mechanism,rationale,"
                    "evidence,status,needs,score,parent_id,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (name[:160], origin, source, channel, offer, mechanism, rationale[:400], (evidence or "")[:400],
                     status, needs, round(m["reach"] * m["pay"] / m["cost"], 4), parent_id, stamp, stamp))
    return cur.rowcount


def generate(sig, market=None):
    """Гипотезы из живых компонентов. Ничего не берётся из воздуха: у каждой — источник и улика."""
    c = _con()
    new = 0
    offers = sig.get("offers") or ["ranked x402 market data ($0.01/query)", "docs/QA for agent tooling"]
    main_offer = offers[0]
    # 1) сабмолты со спросом × механизмы reply_demand / offer_post
    demand = {s: n for s, n in sig.get("demand_by_submolt", [])}
    addr_ok = set(sig.get("address_ok_submolts") or [])
    for sub, n in demand.items():
        new += _propose(c, f"reply demand in m/{sub}", "combinatorial", f"submolt:{sub}", "moltbook_comment", main_offer,
                        "reply_demand", f"в m/{sub} найдено {n} постов с намерением платить", f"moltbook_demand submolt={sub}")
        if sub in addr_ok:
            new += _propose(c, f"offer post in m/{sub} with address", "combinatorial", f"submolt:{sub}", "moltbook_post",
                            main_offer, "offer_post", f"в m/{sub} адреса кошельков живут, спрос {n} постов",
                            f"moltbook_address_policy {sub}")
    # 2) индексы и доски из реестра каналов
    for key, kind, platform, addr, results in sig.get("channels_alive", []):
        # ТОЛЬКО ПОДТВЕРЖДЁННЫЙ ИНДЕКС, НЕ КАНДИДАТ.
        #
        # kind='index_candidate' — это репозиторий GitHub, чьё имя или описание
        # совпало с /market|registry|index|scan|.../ (dealer.py). Это НЕ индекс с
        # API регистрации, а всего лишь зацепка. Предлагать «be listed in
        # <github-username>» по такой зацепке значило плодить мусорные гипотезы,
        # которые исполнитель всё равно не может выполнить (seek_indexes знает
        # девять реальных индексов), — и они же давали ложные «kept» выше.
        # Кандидат станет 'index' только когда найдётся настоящий API размещения.
        if kind == "index":
            needs = CHANNEL_NEEDS["facilitator_adapter"] if platform in ("payai_discovery", "bazaar") else None
            new += _propose(c, f"be listed in {platform}", "combinatorial", f"index:{platform}", "index_api", main_offer,
                            "listing", f"индекс/рынок жив; результатов {results or 0}", key, needs=needs)
        if kind in ("task_board", "escrow_task_board") or platform == "taskmarket":
            new += _propose(c, f"claim escrow tasks on {platform}", "combinatorial", f"board:{platform}", "taskmarket_cli",
                            "docs and small tools", "escrow_claim", "деньги внесены в эскроу за задачи нашего класса",
                            key, needs=CHANNEL_NEEDS["taskmarket_cli"])
        if kind == "agent_board":
            new += _propose(c, f"deliver to {platform} board", "combinatorial", f"board:{platform}", "aibtc_submit",
                            "census/docs/cross-post", "delivery", "доска платит агентам после приёмки", key)
    # 3) источники контрагентов × reciprocal (продавцы-агенты с деньгами)
    for source, n, funds, replies, paid in sig.get("counterparties_by_source", []):
        if source in ("moltbook_demand", "moltbook_inbound") and n:
            new += _propose(c, f"reciprocal calls with agent sellers from {source}", "combinatorial", source,
                            "moltbook_comment", "one /search query for one call of theirs", "reciprocal",
                            f"{n} контрагентов, средний сигнал денег {funds}", source)
        if source == "x402scan_buyers" and n:
            new += _propose(c, "reach x402 buyers where they shop (facilitator feeds)", "combinatorial", source,
                            "facilitator_adapter", main_offer, "listing",
                            f"{n} активных плательщиков; экосистемы: {str(sig.get('payer_ecosystems'))[:120]}",
                            "x402scan buyers", needs=CHANNEL_NEEDS["facilitator_adapter"])
    # 4) доски наград из очереди
    for repo, n, usd in sig.get("bounties_by_source", []):
        new += _propose(c, f"claim declared bounties on {repo}", "combinatorial", f"board:{repo}", "github_issue",
                        "docs/tooling work", "bounty_claim", f"{n} объявленных наград на ${usd}", repo)
    # 5) механизмы, подсмотренные у рынка
    for m in (market or {}).get("mechanisms", []):
        new += _propose(c, f"observed: {m['extract'][:90]}", "observed", f"submolt:{m['submolt']}", "moltbook_comment",
                        main_offer, "reply_demand", "другой агент описал, как получил деньги — проверить тот же путь",
                        f"post {m['post_id']} by {m['author']}: {m['extract'][:200]}")
    # 6) мутации удачных: меняется одно измерение
    for hid, name, source, channel, offer, mech in c.execute(
            "SELECT id, name, source, channel, offer, mechanism FROM strategy_hypotheses WHERE status='kept'").fetchall():
        for alt in [o for o in offers if o != offer][:2]:
            new += _propose(c, f"{name} · offer {alt[:40]}", "mutation", source, channel, alt, mech,
                            "ветвь от работающей гипотезы: другое предложение тому же источнику", f"parent {hid}", parent_id=hid)
        for alt_mech in [m for m in ("offer_post", "reciprocal") if m != mech]:
            new += _propose(c, f"{name} · via {alt_mech}", "mutation", source, channel, offer, alt_mech,
                            "ветвь от работающей гипотезы: другой механизм для того же источника", f"parent {hid}", parent_id=hid)
    c.commit(); c.close()
    return new


# ═══════════════════════════════════════════ ОЦЕНИТЬ
def prioritize(sig):
    """Пересчёт ожидаемой ценности по сигналам: ответы и поступления двигают приоры."""
    c = _con()
    replied_sources = {s for s, n, f, r, p in sig.get("counterparties_by_source", []) if (r or 0) > 0}
    paid_sources = {s for s, n, f, r, p in sig.get("counterparties_by_source", []) if (p or 0) > 0}
    blocked = {k for k, e in sig.get("channels_blocked", [])}
    n = 0
    for hid, source, channel, mech, tries, signal, status in c.execute(
            "SELECT id, source, channel, mechanism, tries, signal, status FROM strategy_hypotheses "
            "WHERE status IN ('proposed','testing','kept')").fetchall():
        m = MECHANISMS.get(mech, {"reach": 0.3, "pay": 0.05, "cost": 1.5})
        reach, pay, cost = m["reach"], m["pay"], m["cost"]
        if source in replied_sources:
            pay = min(0.9, pay * 2)
        if source in paid_sources:
            pay = min(0.95, pay * 4)
        if any(b.startswith(f"moltbook:{source.split(':')[-1]}") for b in blocked):
            reach *= 0.5
        # байесовская поправка по собственному опыту гипотезы
        pay = (pay * 2 + (signal or 0)) / (2 + (tries or 0))
        score = round(reach * pay / cost, 4)
        c.execute("UPDATE strategy_hypotheses SET score=?, updated_at=? WHERE id=?", (score, now(), hid))
        n += 1
    c.commit(); c.close()
    return n


# ═══════════════════════════════════════════ ПРОВЕРИТЬ
def _run_experiment(h):
    """Одна попытка гипотезы — существующими инструментами и их пределами. Возвращает (действие, исход, сигнал)."""
    mech, channel, source = h["mechanism"], h["channel"], h["source"] or ""
    sub = source.split(":", 1)[1] if source.startswith("submolt:") else None
    try:
        if mech in ("reply_demand", "reciprocal") and channel == "moltbook_comment":
            from agents import dealer
            dealer.survey(); dealer.model()
            a = dealer.act(dry_run=False, limit=1)
            sent = len(a.get("sent") or [])
            return "dealer.act", json.dumps(a, ensure_ascii=False)[:200], sent
        if mech == "offer_post" and channel == "moltbook_post" and sub:
            from agents import dealer
            from core import moltbook
            try:
                snap = json.loads((ROOT / "worker" / "snapshot.json").read_text(encoding="utf-8"))
            except Exception:
                snap = {}
            guard.check_action("moltbook_publish", "YELLOW")
            title = f"Ranked x402 market data with a receipt — {snap.get('size', '15k+')} services, $0.01 per query"
            body = (f"What you get: a ranked read of the live x402 market for any capability, from a {snap.get('size', '15k+')}-service "
                    f"index refreshed from the Coinbase discovery feed and scored by real 30-day calls. Every answer carries a "
                    f"receipt: snapshot hash {snap.get('sha256', '')}, generated {str(snap.get('generatedAt', ''))[:16]}, window, "
                    f"scoring revision.\n\nWhat we ask: $0.01 for one /search query (USDC on Base via x402, or the same value in ETH, USDT, BTC, SOL or TRX); reports $0.10 and $0.50; "
                    f"full export $1.25. Nothing to sign up for: call {dealer.SERVICE_URL}/search?q=<capability> and the 402 "
                    f"response settles it.\n\nVerify first, free: {dealer.SERVICE_URL}/sample returns three ranked results with the "
                    f"same receipt; {dealer.SERVICE_URL}/health shows the live snapshot hash.\n\nPay to "
                    f"{__import__('core.payment', fromlist=['OWNER_DESTINATIONS']).OWNER_DESTINATIONS['evm']} (USDC, USDT, DAI or ETH on Base/Ethereum; BTC, SOL and TRX addresses on request) if you "
                    f"prefer a plain transfer; reply here with the capability you need and we answer within a day.")
            res = moltbook.create_post("strategist", title, body, submolt=sub, allow_own_links=True)
            ok = bool(res.get("published")) or res.get("state") == "CONFIRMED"
            return "moltbook.create_post", f"{res.get('state')} {res.get('external_id')}", int(ok)
        if mech in ("listing", "reciprocal") and channel in (
                "index_api", "listing", "x402scan", "Merit-Systems", "agent-souk",
                "mission69b", "public channel found", "public listing on mission69b",
                "direct API integration"):
            # РЕГИСТРАЦИЯ В ИНДЕКСЕ ИДЕМПОТЕНТНА, поэтому её безопасно исполнять и в
            # облаке, и повторно: второй раз тот же origin просто пере-листится.
            # Раньше сюда попадал только channel=index_api, а 30+ гипотез с теми же
            # по смыслу каналами (listing, x402scan, Merit-Systems…) валились в noop
            # и метились «blocked: capability gap» — то есть внешнее действие, которое
            # МОЖНО сделать бесплатно, не делалось из-за несовпадения ярлыка канала.
            from agents import dealer
            rep = dealer.seek_indexes(discover=False)
            plat = source.split(":", 1)[-1]
            # СЧИТАЕМ ТОЛЬКО СОБСТВЕННЫЙ ВЕРДИКТ ПЛОЩАДКИ, НЕ ВЕСЬ ОТЧЁТ.
            #
            # Тонкая, но дорогая ошибка, найденная адверсарной проверкой аудита:
            # при отсутствии plat в отчёте здесь стояло hit=str(rep) — а в отчёте
            # ВСЕГДА есть чужое «x402scan: registered 11/14». Подстрока «registered»
            # красила ЛЮБУЮ гипотезу как успех (signal=1). Так родились 17 ложных
            # «kept» (площадки-то нет в отчёте) и 84 мутации от них — ровно тот
            # мусор, который агенты потом переписывали обратно в план.
            #
            # Нет площадки в отчёте — мы НЕ знаем, что зарегистрировались: signal=0.
            # Положительным считаем только её собственное «registered/в индексе»,
            # и то лишь если рядом нет «нас нет / не найден / не отвечает».
            verdict = str(rep.get(plat, "")) if plat in rep else ""
            vl = verdict.lower()
            positive = any(k in vl for k in ("registered", "в индексе", "listed", "размещено"))
            negative = any(k in vl for k in ("нас нет", "не отвеч", "не найден", "мёртв", "мертв", "неизвест"))
            done = int(positive and not negative)
            return "dealer.seek_indexes", (verdict or f"{plat}: нет в отчёте seek_indexes")[:200], done
        if mech == "board_import":
            from agents import dealer
            r = dealer.import_taskmarket()
            return "dealer.import_taskmarket", json.dumps(r, ensure_ascii=False)[:200], int((r.get("new") or 0) > 0)
        if mech == "bounty_claim":
            from agents import craftsman
            out = craftsman.pursue(dry_run=False)
            return "craftsman.pursue", str(out)[:200], int("заявка" in str(out) or "claim" in str(out).lower())
        if mech == "delivery":
            from agents import bounty
            out = bounty.deliver_aibtc()
            return "bounty.deliver_aibtc", str(out)[:200], int("сдано" in str(out))
    except Exception as e:
        return "error", f"{type(e).__name__}: {str(e)[:160]}", 0
    return "noop", "нет исполнителя для этой связки — capability gap", 0


def experiment(max_runs=2):
    """Запускает лучшие гипотезы (по одной попытке), оценивает исчерпавшие бюджет."""
    c = _con()
    rows = c.execute("SELECT id, name, source, channel, offer, mechanism, tries, budget, signal, status FROM strategy_hypotheses "
                     "WHERE status IN ('proposed','testing') ORDER BY score DESC, id ASC LIMIT 12").fetchall()
    c.close()
    ran, kept, killed = [], [], []
    for r in rows:
        if len(ran) >= max_runs:
            break
        h = dict(zip(("id", "name", "source", "channel", "offer", "mechanism", "tries", "budget", "signal", "status"), r))
        action, outcome, sig = _run_experiment(h)
        c = _con()
        c.execute("INSERT INTO strategy_runs(hypothesis_id,at,action,outcome,signal) VALUES (?,?,?,?,?)",
                  (h["id"], now(), action, outcome, sig))
        if action == "noop":
            c.execute("UPDATE strategy_hypotheses SET status='blocked', needs=?, updated_at=? WHERE id=?",
                      ("нет исполнителя для связки — нужен адаптер (задача механику)", now(), h["id"]))
        else:
            tries = (h["tries"] or 0) + 1
            signal = (h["signal"] or 0) + sig
            status = "testing"
            if tries >= (h["budget"] or 3):
                status = "kept" if signal > 0 else "killed"
            c.execute("UPDATE strategy_hypotheses SET tries=?, signal=?, status=?, last_run_at=?, updated_at=? WHERE id=?",
                      (tries, signal, status, now(), now(), h["id"]))
            (kept if status == "kept" else killed if status == "killed" else []).append(h["name"])
        c.commit(); c.close()
        ran.append(f"{h['name'][:40]} → {action}:{sig}")
    return {"ran": ran, "kept": kept, "killed": killed}


# ═══════════════════════════════════════════ ПРОСИТЬ
def ask_for_capabilities():
    """Заблокированные гипотезы с общей нехваткой → одна задача/эскалация на нехватку."""
    from agents import bounty
    c = _con()
    rows = c.execute("SELECT needs, COUNT(*), GROUP_CONCAT(name, ' | ') FROM strategy_hypotheses "
                     "WHERE status='blocked' AND needs IS NOT NULL GROUP BY needs").fetchall()
    c.close()
    raised = 0
    for needs, n, names in rows:
        if bounty._flag_once("strategist", needs[:80],
                             f"Стратег: {n} гипотез заблокированы одной нехваткой — {needs}",
                             {"гипотезы": names[:600], "что даст": "новые пути к платежу, уже измеренные как живые"}):
            raised += 1
    return raised


# ═══════════════════════════════════════════ СОСТОЯНИЕ И ЦИКЛ
def state(write=True):
    c = _con()
    by = {r[0]: r[1] for r in c.execute("SELECT status, COUNT(*) FROM strategy_hypotheses GROUP BY status")}
    top = [dict(zip(("name", "mechanism", "score", "status", "tries", "signal"), r)) for r in c.execute(
        "SELECT name, mechanism, score, status, tries, signal FROM strategy_hypotheses WHERE status<>'killed' "
        "ORDER BY score DESC LIMIT 8")]
    blocked = [dict(zip(("name", "needs"), r)) for r in c.execute(
        "SELECT name, needs FROM strategy_hypotheses WHERE status='blocked' ORDER BY score DESC LIMIT 6")]
    st = {"at": now(), "hypotheses": by, "top": top, "blocked": blocked}
    if write:
        c.execute("INSERT INTO strategist_state(at,state) VALUES (?,?)", (st["at"], json.dumps(st, ensure_ascii=False)))
        c.execute("DELETE FROM strategist_state WHERE id NOT IN (SELECT id FROM strategist_state ORDER BY id DESC LIMIT 100)")
        c.commit()
    c.close()
    return st


def think(max_runs=5, with_market=True):
    """Один оборот мышления: наблюдать → придумать → оценить → ИСПОЛНИТЬ → отобрать → просить.

    max_runs поднят с 2 до 5: очередь в 169 предложенных гипотез при двух исполнениях
    за оборот не убывала никогда — предлагали быстрее, чем исполняли. Пять безопасно,
    потому что настоящий предел ставят не эти числа, а суточные лимиты в guard.CAPS
    (moltbook_publish=1, moltbook_comment=5): исчерпав их, лишние попытки просто
    возвращают «capped», а не шлют наружу. Идемпотентная часть (регистрация в индексе)
    безопасна и при повторе.
    """
    guard.check_action("research", "GREEN")
    sig = observe()
    market = None
    if with_market:
        try:
            market = observe_market()
        except Exception as e:
            market = {"error": type(e).__name__}
    new = generate(sig, market)
    scored = prioritize(sig)
    ex = experiment(max_runs=max_runs)
    asked = ask_for_capabilities()
    st = state()
    by = st["hypotheses"]
    msg = (f"hypotheses {sum(by.values())} (new {new}; proposed {by.get('proposed', 0)}, testing {by.get('testing', 0)}, "
           f"kept {by.get('kept', 0)}, killed {by.get('killed', 0)}, blocked {by.get('blocked', 0)}); "
           f"market: {(market or {}).get('earn_stories', 0)} earn stories, {len((market or {}).get('mechanisms', []))} mechanisms, "
           f"demand +{(market or {}).get('demand_new', 0)}; ran {len(ex['ran'])}: {'; '.join(ex['ran'])[:160]}; "
           f"asked {asked}")
    if ex["kept"]:
        bus.broadcast("orchestrator", f"Гипотеза подтверждена сигналом и оставлена: {', '.join(ex['kept'])[:200]} — от неё пойдут ветви.")
    return msg


def report():
    st = state(write=False)
    top = "; ".join(f"{t['name'][:45]} [{t['mechanism']}] {t['score']} {t['status']}" for t in st["top"][:5]) or "-"
    return f"стратег: гипотез {st['hypotheses']}; лучшие: {top}; заблокировано: {len(st['blocked'])}"


if __name__ == "__main__":
    print(think(max_runs=int(sys.argv[1]) if len(sys.argv) > 1 else 1))
    print(report())
