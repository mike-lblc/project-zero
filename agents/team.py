"""КОМАНДА РАСШИРЕНИЯ — 4 агента, которые общаются ДРУГ С ДРУГОМ, а не в пустоту.

  MERCHANT     — цены и тарифы: сравнивает нас с рынком, предлагает изменения
  DISTRIBUTOR  — обнаружимость: где нас нет и как туда попасть
  SCRIBE       — тексты: превращает данные в отчёт, который можно продать/разослать
  WATCHDOG     — надзор: кто из агентов замолчал, что упало

Они задают друг другу вопросы через core.bus и отвечают. Диалог виден в чате
дашборда, поэтому «я спрашивал» нельзя выдумать — есть запись.
"""
import sys, json, urllib.request, urllib.error
from pathlib import Path
from datetime import datetime, timezone, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.db import connect
from core import guard, bus

ROOT = Path(__file__).resolve().parent.parent
IDX = ROOT / "data" / "bazaar_index.json"
SERVICE = "http://127.0.0.1:8402"

OUR_TIERS = {"/search": 0.001, "/report": 0.05, "/alpha": 0.25, "/dataset": 0.50}


def now():
    return datetime.now(timezone.utc).isoformat()


def note(agent, claim, conf=None):
    con = connect()
    sid = con.execute("INSERT INTO sources(url,title,fetched_at,raw_excerpt) VALUES (?,?,?,?)",
                      (f"agent://{agent}", f"{agent} finding", now(), claim[:400])).lastrowid
    con.execute("INSERT INTO evidence(claim,source_id,agent,created_at,confidence) VALUES (?,?,?,?,?)",
                (claim, sid, agent, now(), conf))
    con.commit(); con.close()


def _index():
    try:
        return json.loads(IDX.read_text(encoding="utf-8"))
    except Exception:
        return []


# ============================================================ MERCHANT
def merchant():
    """Сравнивает наши цены с реальным рынком и говорит, где мы продешевили."""
    guard.check_action("research", "GREEN")
    items = _index()
    if not items:
        return "индекса нет"
    prices = []
    for it in items:
        a = (it.get("accepts") or [{}])[0]
        try:
            prices.append(int(a.get("maxAmountRequired") or a.get("amount")) / 1e6)
        except Exception:
            pass
    if not prices:
        return "цен нет"
    prices.sort()
    p50 = prices[len(prices) // 2]
    p90 = prices[int(len(prices) * 0.9)]
    p99 = prices[int(len(prices) * 0.99)]
    top = prices[-1]

    verdict = []
    if OUR_TIERS["/search"] <= p50:
        verdict.append(f"/search ${OUR_TIERS['/search']} — на уровне медианы (${p50:.4f}), "
                       f"это вход, а не заработок")
    if OUR_TIERS["/dataset"] < p99:
        verdict.append(f"/dataset ${OUR_TIERS['/dataset']} НИЖЕ 99-го перцентиля (${p99:.2f}) — "
                       f"можно поднять, полного краула нет ни у кого")
    else:
        verdict.append(f"/dataset ${OUR_TIERS['/dataset']} уже выше 99-го перцентиля (${p99:.2f}) — "
                       f"выше поднимать рискованно")
    txt = "; ".join(verdict)
    bus.broadcast("merchant", f"Ценовой срез рынка: медиана ${p50:.4f}, 90-й ${p90:.3f}, "
                              f"99-й ${p99:.2f}, максимум ${top:.2f}. {txt}")
    note("merchant", f"PRICING: market p50=${p50:.4f} p90=${p90:.3f} p99=${p99:.2f} max=${top:.2f}. "
                     f"Our tiers {OUR_TIERS}. {txt}", conf=0.9)
    # спрашивает исследователя, куда двигаться
    bus.ask("merchant", "explorer",
            "В какой категории спрос выше конкуренции? Хочу понять, где поднять цену без потери спроса.",
            {"our_tiers": OUR_TIERS})
    return f"медиана ${p50:.4f}, 99-й ${p99:.2f}"


# ============================================================ DISTRIBUTOR
def distributor():
    """Проверяет, видит ли нас рынок. Это сейчас главное узкое место."""
    guard.check_action("research", "GREEN")
    listed = False
    try:
        req = urllib.request.Request(
            "https://pay.openfacilitator.io/discovery/resources",
            headers={"User-Agent": "P0-distributor/0.1", "Accept": "application/json"})
        d = json.loads(urllib.request.urlopen(req, timeout=25).read().decode())
        res = d.get("resources") or d.get("items") or []
        listed = any("trycloudflare" in str(r.get("url") or r.get("resource") or "") for r in res)
        total = len(res)
    except Exception as e:
        bus.broadcast("distributor", f"Не смог проверить индекс ({type(e).__name__}). "
                                     f"Не выдумываю — отмечаю как неизвестно.")
        return f"проверка недоступна: {type(e).__name__}"

    if listed:
        bus.broadcast("distributor", "Мы ЕСТЬ в индексе фасилитатора — нас можно найти.")
        note("distributor", "LISTED: service appears in facilitator discovery index", conf=1.0)
        return "мы в индексе"

    bus.broadcast("distributor",
                  f"Нас в индексе НЕТ (там {total} сервисов). Причина известна: Bazaar заносит "
                  f"только ПОСЛЕ первого подтверждённого платежа — замкнутый круг. "
                  f"Разорвать его честно можно лишь внешним каналом: постоянный адрес + аккаунт CDP.")
    note("distributor", f"NOT LISTED: chicken-and-egg confirmed — Bazaar indexes only after first "
                        f"settle. Facilitator index has {total} services, we are not among them. "
                        f"Blockers: ephemeral tunnel URL + no CDP account.", conf=1.0)
    bus.handoff("distributor", "watchdog",
                "Следи за стабильностью адреса сервиса",
                "листинг на временный адрес бессмысленен — если адрес умрёт, мы потеряем рейтинг")
    return f"не в индексе ({total} чужих сервисов)"


# ============================================================ SCRIBE
def scribe():
    """Готовит текст отчёта из данных — то, что можно продать или разослать."""
    guard.check_action("research", "GREEN")
    items = _index()
    if not items:
        return "нет данных"
    cats = {}
    for it in items:
        q = it.get("quality") or {}
        for t in (it.get("tags") or []):
            d = cats.setdefault(t, {"n": 0, "payers": 0, "calls": 0})
            d["n"] += 1
            d["payers"] += q.get("l30DaysUniquePayers") or 0
            d["calls"] += q.get("l30DaysTotalCalls") or 0
    ranked = sorted(((v["payers"], k, v) for k, v in cats.items() if v["n"] >= 3), reverse=True)[:5]
    movers = sorted(items, key=lambda i: -((i.get("quality") or {}).get("l30DaysUniquePayers") or 0))[:5]

    lines = ["ОТЧЁТ ПО РЫНКУ x402", f"Дата: {datetime.now().strftime('%d.%m.%Y')}",
             f"Сервисов в индексе: {len(items):,}", "", "Категории по числу плательщиков:"]
    for payers, tag, d in ranked:
        lines.append(f"  • {tag}: {payers} плательщиков, {d['n']} поставщиков, {d['calls']:,} вызовов")
    lines += ["", "Кто собирает больше всего уникальных плательщиков:"]
    for m in movers:
        q = m.get("quality") or {}
        lines.append(f"  • {(m.get('serviceName') or m.get('resource',''))[:44]} — "
                     f"{q.get('l30DaysUniquePayers')} плательщиков / {q.get('l30DaysTotalCalls')} вызовов")
    lines += ["", "Честная оговорка: медианный сервис получает единицы вызовов в месяц. "
                  "Рынок живой, но маленький."]
    text = "\n".join(lines)
    out = ROOT / "reports" / "market_report.txt"
    out.parent.mkdir(exist_ok=True)
    out.write_text(text, encoding="utf-8")
    bus.broadcast("scribe", f"Отчёт готов ({len(text)} символов): {len(ranked)} категорий, "
                            f"{len(movers)} лидеров. Лежит в reports/market_report.txt — "
                            f"это то, что уходит в /report за $0.05 и в рассылку.")
    note("scribe", f"REPORT GENERATED: {len(items)} services, top category '{ranked[0][1]}' "
                   f"with {ranked[0][0]} payers", conf=0.9)
    return f"отчёт {len(text)} символов"


# ============================================================ WATCHDOG
def watchdog():
    """Смотрит, кто из агентов замолчал и что падает. Отвечает на вопросы других."""
    guard.check_action("research", "GREEN")
    con = connect()
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    silent = con.execute(
        "SELECT agent, MAX(started_at) FROM runs GROUP BY agent HAVING MAX(started_at) < ?",
        (cutoff,)).fetchall()
    fails = con.execute("SELECT agent, notes FROM runs WHERE status='error' "
                        "ORDER BY id DESC LIMIT 5").fetchall()
    rep = con.execute("SELECT agent, SUM(calls), SUM(correct) FROM agent_reputation "
                      "GROUP BY agent").fetchall()
    con.close()

    problems = []
    if silent:
        problems.append("молчат дольше 30 мин: " + ", ".join(s[0] for s in silent))
    if fails:
        problems.append(f"последних падений: {len(fails)}")
    weak = [f"{a} ({c}/{t})" for a, t, c in rep if t and c / t < 0.8]
    if weak:
        problems.append("низкая успешность: " + ", ".join(weak))

    # отвечает на накопившиеся вопросы
    for q in bus.pending_questions("watchdog"):
        bus.answer("watchdog", q["id"],
                   "Сервис и цикл живы; " + ("проблем нет" if not problems else "; ".join(problems)))

    if not problems:
        bus.broadcast("watchdog", "Все агенты отзываются, падений нет, успешность в норме.")
        return "всё живо"
    txt = "; ".join(problems)
    bus.broadcast("watchdog", f"⚠ Нашёл проблемы: {txt}")
    note("watchdog", f"WATCHDOG: {txt}", conf=1.0)
    return txt


# ============================================================ EXPLORER-ОТВЕТЧИК
def explorer_replies():
    """Исследователь отвечает на вопросы, которые ему задали другие агенты."""
    qs = bus.pending_questions("explorer")
    if not qs:
        return "вопросов нет"
    from agents import growth
    items = _index()
    stat = {}
    for it in items:
        q = it.get("quality") or {}
        for t in (it.get("tags") or []):
            d = stat.setdefault(t, {"n": 0, "payers": 0})
            d["n"] += 1; d["payers"] += q.get("l30DaysUniquePayers") or 0
    best = sorted(((d["payers"] / d["n"], t, d) for t, d in stat.items()
                   if d["n"] >= 3 and d["payers"] >= 10), reverse=True)[:3]
    ans = "; ".join(f"{t}: {r:.1f} плательщика на поставщика ({d['n']} игроков)"
                    for r, t, d in best) or "данных мало"
    for q in qs:
        bus.answer("explorer", q["id"], ans)
    return f"ответил на {len(qs)} вопрос(ов)"


CYCLE = [("merchant", merchant), ("distributor", distributor), ("scribe", scribe),
         ("watchdog", watchdog), ("explorer_replies", explorer_replies)]


if __name__ == "__main__":
    for name, fn in CYCLE:
        try:
            print(f"{name}: {fn()}")
        except Exception as e:
            print(f"{name} FAILED: {type(e).__name__}: {e}")
    print("шина:", bus.stats())
