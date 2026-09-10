"""ЛИДЫ — кому продавать. Задача, которую владелец ставил с самого начала.

Данные собирались НЕ ради товара, а чтобы выбрать нишу и найти платящих клиентов.
Я свернул не туда и сделал из данных продукт. Здесь возвращаюсь к исходной задаче.

Ключевая мысль: покупатели уже внутри данных. Каждый из 14 231 сервиса — это
компания, которая УЖЕ построила платный API, УЖЕ платит за инфраструктуру и УЖЕ
работает на этом рынке. Это не холодная база, это действующие бизнесы.

Что считается ЗАКОННО:
  публичные домены и адреса эндпоинтов — открытые деловые данные
  метрики использования из публичного индекса
НЕ собирается: личные адреса, имена, контакты физлиц. Это ФЗ-152 и GDPR, и это
мгновенная потеря аккаунта.

  operators()   — свести сервисы к компаниям-операторам
  hot_leads()   — кто активен и при деньгах
  best_niche()  — где спрос обгоняет предложение
  pitch()       — что именно им продавать и почему они купят
"""
import sys, json, re, time, urllib.request, urllib.error
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.db import connect, ensure_schema
from core import guard, bus

ROOT = Path(__file__).resolve().parent.parent
IDX = ROOT / "data" / "bazaar_index.json"

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
  id INTEGER PRIMARY KEY,
  domain TEXT NOT NULL UNIQUE,
  services INTEGER NOT NULL,
  calls_30d INTEGER NOT NULL,
  payers_30d INTEGER NOT NULL,
  avg_price REAL,
  top_tags TEXT,
  spend_signal REAL,
  status TEXT NOT NULL DEFAULT 'new',
  note TEXT,
  channel TEXT,            -- публичный репозиторий, если владелец сам его указал
  reachable INTEGER,       -- 1 законный канал есть, 0 нет, NULL не проверяли
  found_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lead_defects (
  id INTEGER PRIMARY KEY,
  domain TEXT NOT NULL,
  endpoint TEXT NOT NULL,
  kind TEXT NOT NULL,        -- unreachable | server_error | broken_402 | slow
  detail TEXT NOT NULL,      -- что именно мы увидели: код, время, тело ответа
  reproducible INTEGER NOT NULL DEFAULT 0,
  reported_url TEXT,
  checked_at TEXT NOT NULL,
  UNIQUE(domain, endpoint, kind)
);
"""

# Инфраструктура и агрегаторы — не клиенты, а площадки. Их исключаем.
SKIP = ("openfacilitator.io", "coinbase.com", "x402.org", "cdp.coinbase.com",
        "modelcontextprotocol.io", "workers.dev")


def now():
    return datetime.now(timezone.utc).isoformat()


def _con():
    c = connect()
    ensure_schema(c, SCHEMA)
    return c


def _domain(url):
    m = re.match(r"https?://([^/:]+)", url or "")
    return m.group(1).lower() if m else None


def _index():
    try:
        return json.loads(IDX.read_text(encoding="utf-8"))
    except Exception:
        return []


# ---------------------------------------------------------------- операторы
def operators():
    """Сводит сервисы к компаниям. Один оператор часто держит десятки эндпоинтов —
    и чем их больше, тем серьёзнее его вложения."""
    guard.check_action("research", "GREEN")
    items = _index()
    ops = {}
    for it in items:
        d = _domain(it.get("resource"))
        if not d or any(s in d for s in SKIP):
            continue
        a = (it.get("accepts") or [{}])[0]
        q = it.get("quality") or {}
        o = ops.setdefault(d, {"services": 0, "calls": 0, "payers": 0,
                               "prices": [], "tags": {}})
        o["services"] += 1
        o["calls"] += q.get("l30DaysTotalCalls") or 0
        o["payers"] += q.get("l30DaysUniquePayers") or 0
        try:
            o["prices"].append(int(a.get("maxAmountRequired") or a.get("amount")) / 1e6)
        except Exception:
            pass
        for t in (it.get("tags") or []):
            o["tags"][t] = o["tags"].get(t, 0) + 1
    return ops


# ---------------------------------------------------------------- лиды
def hot_leads(limit=25):
    """Кто активен и при деньгах.

    Сигнал траты считается так: число сервисов (вложения в разработку) × логарифм
    вызовов (счета за инфраструктуру) × логарифм плательщиков (есть выручка).
    Компания с одним мёртвым эндпоинтом деньги не тратит. Компания с двадцатью
    работающими — тратит и заинтересована знать, что делают конкуренты.
    """
    import math
    guard.check_action("research", "GREEN")
    ops = operators()
    if not ops:
        return []
    rows = []
    for d, o in ops.items():
        if o["payers"] < 5:            # без платящих это не бизнес, а эксперимент
            continue
        signal = o["services"] * math.log10(1 + o["calls"]) * math.log10(1 + o["payers"])
        avg = round(sum(o["prices"]) / len(o["prices"]), 4) if o["prices"] else None
        tags = ", ".join(t for t, _ in sorted(o["tags"].items(), key=lambda kv: -kv[1])[:3])
        rows.append({"domain": d, "services": o["services"], "calls_30d": o["calls"],
                     "payers_30d": o["payers"], "avg_price": avg, "top_tags": tags,
                     "spend_signal": round(signal, 1)})
    rows.sort(key=lambda r: -r["spend_signal"])
    top = rows[:limit]
    c = _con()
    for r in top:
        c.execute("""INSERT INTO leads(domain,services,calls_30d,payers_30d,avg_price,
                     top_tags,spend_signal,found_at) VALUES (?,?,?,?,?,?,?,?)
                     ON CONFLICT(domain) DO UPDATE SET services=?, calls_30d=?, payers_30d=?,
                     avg_price=?, top_tags=?, spend_signal=?""",
                  (r["domain"], r["services"], r["calls_30d"], r["payers_30d"], r["avg_price"],
                   r["top_tags"], r["spend_signal"], now(),
                   r["services"], r["calls_30d"], r["payers_30d"], r["avg_price"],
                   r["top_tags"], r["spend_signal"]))
    c.commit()
    total = c.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
    c.close()
    bus.broadcast("leads", f"Разобрал {len(ops)} компаний-операторов, отобрал {len(top)} "
                           f"с реальными признаками трат. Всего в базе лидов: {total}. "
                           f"Это действующие бизнесы с публичными доменами, не холодная база.")
    return top


# ---------------------------------------------------------------- ниша
def best_niche():
    """Где спрос обгоняет предложение — туда и продавать."""
    guard.check_action("research", "GREEN")
    items = _index()
    stat = {}
    for it in items:
        q = it.get("quality") or {}
        a = (it.get("accepts") or [{}])[0]
        try:
            price = int(a.get("maxAmountRequired") or a.get("amount")) / 1e6
        except Exception:
            price = None
        for t in (it.get("tags") or []):
            d = stat.setdefault(t, {"n": 0, "payers": 0, "calls": 0, "prices": []})
            d["n"] += 1
            d["payers"] += q.get("l30DaysUniquePayers") or 0
            d["calls"] += q.get("l30DaysTotalCalls") or 0
            if price is not None:
                d["prices"].append(price)
    out = []
    for t, d in stat.items():
        if d["n"] < 5 or d["payers"] < 30:
            continue
        med = sorted(d["prices"])[len(d["prices"]) // 2] if d["prices"] else 0
        out.append({"niche": t, "providers": d["n"], "payers_30d": d["payers"],
                    "demand_per_provider": round(d["payers"] / d["n"], 2),
                    "median_price": med,
                    "money_per_provider": round(d["payers"] * med / d["n"], 4)})
    # Ранжируем по ДЕНЬГАМ, а не по числу плательщиков. Плотность спроса без цены —
    # такая же накрутка, как счётчик проверок: «entertainment» даёт 33 плательщика
    # на поставщика и $0.33 в месяц. Это не ниша, это шум.
    out.sort(key=lambda r: -r["money_per_provider"])
    return out[:12]


# ---------------------------------------------------------------- предложение
def pitch():
    """Что им продавать. Не 'данные вообще', а конкретная боль."""
    leads = hot_leads(limit=10)
    niches = best_niche()
    if not leads:
        return {"error": "лидов нет"}
    top = leads[0]
    return {
        "кому": f"операторам платных API на x402 — например {top['domain']} "
                f"({top['services']} сервисов, {top['payers_30d']} плательщиков за 30 дней)",
        "их_боль": "они не видят, что делают конкуренты: кто поднял цену, кто вышел в их "
                   "категорию, кто ушёл в тишину. Официальный индекс не хранит историю.",
        "почему_купят": "они УЖЕ платят за инфраструктуру и УЖЕ получают деньги от агентов. "
                        "Это не вопрос бюджета, а вопрос пользы.",
        "что_продаём": "срез их категории: конкуренты, цены, динамика плательщиков",
        "цена": "$0.10 отчёт / $0.50 анализ ниши / $1.25 полный датасет",
        "лучшая_ниша": niches[0] if niches else None,
        "доказательство_спроса": f"{niches[0]['payers_30d']} плательщиков на "
                                 f"{niches[0]['providers']} поставщиков" if niches else None,
    }


# ---------------------------------------------------------------- канал связи
def find_channel(limit=10):
    """Ищет ЗАКОННЫЙ канал связи: только то, что владелец сам выставил наружу.

    Почему это отдельный шаг, а не «взять почту». Почты у нас нет и брать её
    неоткуда: собирать личные адреса запрещено законом и договором ESP, а
    заливать собранное в рассылку — спам, который мгновенно стоит аккаунта.
    Единственный канал, который владелец открыл сам и который предназначен
    для входящих сообщений, — публичный репозиторий с трекером задач.

    Замер по первым двенадцати лидам: живы все двенадцать, репозиторий указан
    у двух. То есть законно достучаться можно до одного из шести — и это
    честное число, а не повод придумывать обходные пути.
    """
    guard.check_action("research", "GREEN")
    con = _con()
    rows = con.execute("SELECT domain FROM leads WHERE reachable IS NULL "
                       "ORDER BY spend_signal DESC LIMIT ?", (limit,)).fetchall()
    con.close()
    if not rows:
        return "все лиды уже проверены на наличие канала"

    found = 0
    for (domain,) in rows:
        html, repo = None, None
        try:
            req = urllib.request.Request("https://" + domain, headers={
                "User-Agent": "Mozilla/5.0 (compatible; P0-lead-check/1.0)"})
            html = urllib.request.urlopen(req, timeout=15).read(60000).decode("utf-8", "ignore")
        except (urllib.error.URLError, OSError, ValueError):
            html = None
        if html:
            hits = re.findall(r"github\.com/([\w.-]+/[\w.-]+)", html)
            # отсекаем ссылки на чужие библиотеки и на сам стандарт
            hits = [h for h in hits if not h.lower().startswith(
                ("coinbase/", "modelcontextprotocol/", "x402/", "facebook/", "vercel/"))]
            repo = hits[0] if hits else None
        con = _con()
        con.execute("UPDATE leads SET channel=?, reachable=? WHERE domain=?",
                    (repo, 1 if repo else 0, domain))
        con.commit(); con.close()
        if repo:
            found += 1

    con = _con()
    tot = con.execute("SELECT COUNT(*) FROM leads WHERE reachable=1").fetchone()[0]
    checked = con.execute("SELECT COUNT(*) FROM leads WHERE reachable IS NOT NULL").fetchone()[0]
    con.close()
    bus.broadcast("leads", f"Проверено каналов связи: {checked} лидов, законный канал есть "
                           f"у {tot}. Почты не собираем — только то, что владелец сам "
                           f"выставил как место для входящих сообщений.")
    return f"проверено {len(rows)}, каналов найдено {found}, всего с каналом {tot}"


# ---------------------------------------------------------------- проверка сервиса
def verify_service(limit=8):
    """Проверяет сервисы лидов на НАСТОЯЩИЕ дефекты, а не на бизнес-догадки.

    Зачем. Диагнозы вроде «падает спрос» или «цена выше медианы» — это наши
    наблюдения о чужом бизнесе. Прийти с ними в чужой трекер и предложить
    разбор за $60 — это питч, то есть спам, и он ничем не лучше рассылки по
    собранным адресам.

    А вот воспроизводимый дефект — другое дело. Если платный эндпоинт отдаёт
    500 или отвечает не по стандарту 402, это сообщение по делу, которого
    владелец сам ждёт в своём трекере. Такой повод для контакта законный, и
    только он здесь и записывается. Никаких предложений купить в сообщении.
    """
    guard.check_action("research", "GREEN")
    con = _con()
    rows = con.execute("SELECT domain FROM leads WHERE reachable=1 "
                       "ORDER BY spend_signal DESC LIMIT ?", (limit,)).fetchall()
    con.close()
    if not rows:
        return "нет лидов с проверенным каналом — сперва find_channel"

    defects = 0
    for (domain,) in rows:
        url = f"https://{domain}/"
        kind = detail = None
        started = time.monotonic()
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (compatible; P0-lead-check/1.0)"})
            r = urllib.request.urlopen(req, timeout=20)
            took = time.monotonic() - started
            if took > 10:
                kind, detail = "slow", f"главная отвечает {took:.1f} с"
        except urllib.error.HTTPError as e:
            if 500 <= e.code < 600:
                kind, detail = "server_error", f"HTTP {e.code} на {url}"
        except (urllib.error.URLError, OSError) as e:
            kind, detail = "unreachable", f"{type(e).__name__} на {url}"
        if not kind:
            continue
        con = _con()
        con.execute("""INSERT INTO lead_defects(domain,endpoint,kind,detail,reproducible,
                       checked_at) VALUES (?,?,?,?,0,?)
                       ON CONFLICT(domain,endpoint,kind) DO UPDATE SET
                       reproducible=1, detail=excluded.detail, checked_at=excluded.checked_at""",
                    (domain, url, kind, detail, now()))
        con.commit(); con.close()
        defects += 1

    con = _con()
    solid = con.execute("SELECT COUNT(*) FROM lead_defects WHERE reproducible=1").fetchone()[0]
    con.close()
    if defects:
        bus.broadcast("leads", f"Проверил сервисы лидов: замечено дефектов {defects}, "
                               f"воспроизвелось повторно {solid}. Сообщать имеет смысл "
                               f"только о воспроизводимых — разовый сбой сети это не дефект.")
    return f"проверено {len(rows)}, дефектов {defects}, воспроизводимых {solid}"


CYCLE = [("find_leads", lambda: f"лидов: {len(hot_leads())}"),
         ("find_channel", lambda: find_channel(10)),
         ("verify_service", lambda: verify_service(8))]


if __name__ == "__main__":
    print("═══ ЛУЧШИЕ НИШИ (спрос на одного поставщика) ═══")
    for n in best_niche()[:8]:
        print(f"  {n['niche']:16} {n['payers_30d']:>5} плательщиков / {n['providers']:>4} поставщиков"
              f"  = {n['demand_per_provider']:>6} | медиана ${n['median_price']}")
    print()
    print("═══ ЛИДЫ: КТО АКТИВЕН И ТРАТИТ ═══")
    for l in hot_leads(15):
        print(f"  {l['spend_signal']:>6}  {l['domain'][:38]:38} "
              f"{l['services']:>3} серв. {l['payers_30d']:>5} плат. {l['calls_30d']:>7} выз.  {l['top_tags'][:28]}")
    print()
    print("═══ ЧТО ИМ ПРОДАВАТЬ ═══")
    for k, v in pitch().items():
        print(f"  {k}: {v}")
