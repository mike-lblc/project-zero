"""ОХОТНИК — агент привлечения. Разделы 12 и 13 директивы.

Он закрывает дыру, из-за которой подписчиков ноль: всё построенное работало
ПОСЛЕ прихода человека, и ничто не приводило человека.

ГРАНИЦЫ, установленные жёстко (директива: «Do not spam. Do not create deceptive
identities. Do not violate platform policies»):

  ищет МЕСТА и ТЕМЫ — где аудитория уже собралась и о чём спрашивает
  НЕ ищет чужие адреса — сбор личных данных незаконен и убивает аккаунт
  НЕ регистрируется и НЕ постит сам — это делает владелец от своего имени

То есть он готовит охоту, а выстрел делает человек. Иначе это спам, а не
привлечение, и мы теряем и домен, и рассылку.

  find_channels()  — где обитает наша аудитория, с проверкой доступности
  demand_signals() — о чём эта аудитория реально спрашивает (по нашим же данным)
  draft_post()     — готовый текст для публикации ВЛАДЕЛЬЦЕМ
  funnel()         — где рвётся воронка прямо сейчас
"""
import sys, json, urllib.request, urllib.error
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.db import connect, ensure_schema
from core import guard, bus

ROOT = Path(__file__).resolve().parent.parent
IDX = ROOT / "data" / "bazaar_index.json"
UA = {"User-Agent": "Mozilla/5.0 (compatible; P0-hunter/0.1)"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS channels (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  url TEXT NOT NULL,
  kind TEXT NOT NULL,            -- registry | community | directory | marketplace
  reachable INTEGER,
  needs_account INTEGER,
  needs_owner INTEGER,           -- 1 = публикует владелец, агент не может
  audience_fit INTEGER,          -- 1..5
  note TEXT,
  checked_at TEXT
);
CREATE TABLE IF NOT EXISTS drafts (
  id INTEGER PRIMARY KEY,
  channel TEXT NOT NULL,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'ready',   -- ready | published | rejected
  created_at TEXT NOT NULL
);
"""

# Каналы, где обитают те, кто строит платные эндпоинты для агентов.
# audience_fit — насколько это НАША аудитория, а не «люди вообще».
CANDIDATES = [
    ("Реестр MCP", "https://registry.modelcontextprotocol.io/v0/servers?limit=1",
     "registry", 0, 0, 5, "Мы уже здесь. Агенты обходят его сами — канал работает без нас"),
    ("Bazaar Coinbase", "https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources?limit=1",
     "registry", 1, 1, 5, "14k+ сервисов, живой трафик. Листинг требует аккаунт CDP"),
    ("OpenFacilitator", "https://pay.openfacilitator.io/discovery/resources",
     "registry", 0, 0, 4, "Без аккаунта, но индекс почти мёртвый"),
    ("moltbook", "https://www.moltbook.com/",
     "community", 1, 1, 5, "Соцсеть ИИ-агентов. Публикует владелец, ранний доступ по заявке"),
    ("x402scan", "https://www.x402scan.com/",
     "directory", 1, 1, 3, "Каталог x402. Подача вручную"),
    ("glama MCP", "https://glama.ai/mcp/servers",
     "directory", 1, 1, 4, "Каталог MCP-серверов, подхватывает из официального реестра"),
    ("mcpservers.org", "https://mcpservers.org",
     "directory", 1, 1, 4, "Ещё один каталог MCP"),
]


def now():
    return datetime.now(timezone.utc).isoformat()


def _con():
    c = connect()
    ensure_schema(c, SCHEMA)
    return c


def _reach(url):
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=15)
        return r.status < 400
    except urllib.error.HTTPError as e:
        return e.code < 500
    except Exception:
        return False


# ---------------------------------------------------------------- 1. каналы
def find_channels():
    """Проверяет каналы на доступность и записывает, что мешает туда попасть."""
    guard.check_action("research", "GREEN")
    c = _con()
    live = 0
    for name, url, kind, acct, owner, fit, note in CANDIDATES:
        ok = _reach(url)
        live += 1 if ok else 0
        c.execute("""INSERT INTO channels(name,url,kind,reachable,needs_account,needs_owner,
                     audience_fit,note,checked_at) VALUES (?,?,?,?,?,?,?,?,?)
                     ON CONFLICT(name) DO UPDATE SET reachable=?, checked_at=?""",
                  (name, url, kind, int(ok), acct, owner, fit, note, now(), int(ok), now()))
    c.commit()
    free = c.execute("SELECT COUNT(*) FROM channels WHERE reachable=1 AND needs_account=0").fetchone()[0]
    blocked = [r[0] for r in c.execute(
        "SELECT name FROM channels WHERE needs_account=1 AND audience_fit>=4")]
    c.close()
    bus.broadcast("hunter",
                  f"Проверил каналы привлечения: {live} из {len(CANDIDATES)} доступны, "
                  f"{free} не требуют аккаунта. Требуют рук владельца: {', '.join(blocked)}.")
    return f"каналов живых {live}/{len(CANDIDATES)}, без аккаунта {free}"


# ---------------------------------------------------------------- 2. спрос
def demand_signals():
    """О чём аудитория реально спрашивает. Считаем по НАШИМ данным: категории,
    где много платящих на мало поставщиков, — это и есть неудовлетворённый спрос."""
    guard.check_action("research", "GREEN")
    try:
        items = json.loads(IDX.read_text(encoding="utf-8"))
    except Exception:
        return "индекса нет"
    stat = {}
    for it in items:
        q = it.get("quality") or {}
        for t in (it.get("tags") or []):
            d = stat.setdefault(t, {"n": 0, "payers": 0})
            d["n"] += 1
            d["payers"] += q.get("l30DaysUniquePayers") or 0
    hot = sorted(((d["payers"] / d["n"], t, d) for t, d in stat.items()
                  if d["n"] >= 3 and d["payers"] >= 20), reverse=True)[:5]
    if not hot:
        return "сигналов недостаточно"
    txt = "; ".join(f"{t} ({r:.1f} плательщика на поставщика)" for r, t, _ in hot)
    bus.broadcast("hunter", f"Где спрос обгоняет предложение: {txt}. "
                            f"Это темы, на которые наша аудитория откликнется.")
    return f"тем со спросом: {len(hot)}"


# ---------------------------------------------------------------- 3. текст
def draft_post(channel="moltbook"):
    """Готовит текст для публикации ВЛАДЕЛЬЦЕМ. Только факты из наших измерений:
    выдуманные цифры убьют доверие быстрее, чем отсутствие поста."""
    guard.check_action("research", "GREEN")
    try:
        items = json.loads(IDX.read_text(encoding="utf-8"))
    except Exception:
        items = []
    calls = sum((i.get("quality") or {}).get("l30DaysTotalCalls") or 0 for i in items)
    payers = sum((i.get("quality") or {}).get("l30DaysUniquePayers") or 0 for i in items)
    prices = sorted(p for p in ((i.get("accepts") or [{}])[0].get("maxAmountRequired") for i in items) if p)
    med = int(prices[len(prices) // 2]) / 1e6 if prices else 0

    title = "Я обошёл весь рынок x402 — вот что агенты покупают на самом деле"
    body = f"""Собрал полный обход публичного индекса x402 и посчитал по метрикам использования,
а не по заявлениям.

Цифры на сегодня:
• сервисов в индексе: {len(items):,}
• вызовов за 30 дней: {calls:,}
• уникальных плательщиков: {payers:,}
• медианная цена вызова: ${med:.4f}

Неудобная часть, о которой почти не пишут: медианный сервис получает считаные вызовы
в месяц. Верхний процент собирает почти весь объём. Рынок настоящий, но маленький —
это стоит знать до того, как строить на нём бизнес, а не после.

Поиск по этим данным выложен как MCP-сервер, базовый запрос бесплатный:
https://x402-bazaar-rank.x402-bazaar-rank-worker.workers.dev/mcp

Если интересны еженедельные срезы — что появилось, кто ушёл в тишину, куда двигаются
цены: https://x402-bazaar-rank.x402-bazaar-rank-worker.workers.dev/join
"""
    c = _con()
    c.execute("INSERT INTO drafts(channel,title,body,created_at) VALUES (?,?,?,?)",
              (channel, title, body, now()))
    c.commit()
    n = c.execute("SELECT COUNT(*) FROM drafts WHERE status='ready'").fetchone()[0]
    c.close()
    out = ROOT / "ops" / f"post_{channel}.txt"
    out.parent.mkdir(exist_ok=True)
    out.write_text(f"{title}\n\n{body}", encoding="utf-8")
    bus.broadcast("hunter", f"Текст для «{channel}» готов и лежит в ops/post_{channel}.txt. "
                            f"Публикует владелец от своего имени — бот, изображающий человека, "
                            f"это спам и потеря площадки. Черновиков наготове: {n}.")
    return f"черновик готов: ops/post_{channel}.txt"


# ---------------------------------------------------------------- 4. воронка
def funnel():
    """Где рвётся путь к подписчику. Считаем по фактам, а не по надеждам."""
    c = _con()
    q = lambda s: c.execute(s).fetchone()[0]
    subs = q("SELECT COUNT(*) FROM subscribers")
    listed = q("SELECT COUNT(*) FROM channels WHERE reachable=1 AND needs_account=0")
    drafts = q("SELECT COUNT(*) FROM drafts WHERE status='ready'")
    published = q("SELECT COUNT(*) FROM drafts WHERE status='published'")
    c.close()
    stages = [("каналов доступно без аккаунта", listed),
              ("текстов готово", drafts),
              ("опубликовано владельцем", published),
              ("подписчиков пришло", subs)]
    broken = next((n for n, v in stages if v == 0), None)
    if broken:
        bus.broadcast("hunter", f"Воронка рвётся здесь: «{broken}» = 0. "
                                f"Всё, что дальше по цепочке, работать не может по определению.")
    return {"stages": stages, "broken_at": broken}


CYCLE = [("hunt_channels", find_channels), ("hunt_demand", demand_signals),
         ("hunt_funnel", funnel)]


if __name__ == "__main__":
    print("=== КАНАЛЫ ===");        print(" ", find_channels())
    print("=== СПРОС ===");         print(" ", demand_signals())
    print("=== ТЕКСТ ===");         print(" ", draft_post())
    print("=== ВОРОНКА ===")
    f = funnel()
    for name, v in f["stages"]:
        print(f"  {name:34} {v}")
    print(f"  РВЁТСЯ НА: {f['broken_at']}")
