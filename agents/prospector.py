"""РАЗВЕДЧИК ЗАРАБОТКА — ищет ВСЕ способы, которыми агенты могут получить деньги.

Зачем он появился. Владелец поймал ровно то, что и должен был поймать: я
уцепился за один путь (баунти) и один приём (скорость), а «антитуннельный»
агент, который должен был это предотвращать, оказался подделкой — он повторял
из цикла в цикл четыре захардкоженные строки и назывался исследованием.

Здесь исследование настоящее, и оно устроено так:

  1. ПРОСТРАНСТВО ПОИСКА. Перечислены классы заработка, а не готовые ответы.
     Класс — это гипотеза «здесь бывают деньги», её ещё надо проверить.
  2. ЖИВЫЕ ПЛОЩАДКИ. По каждому классу идёт настоящий поиск, и найденные
     площадки сохраняются вместе с источником. Путь без источника НЕ
     ЗАПИСЫВАЕТСЯ — это то же правило, что и для всех наших утверждений.
  3. ПРОВЕРКА О СТЕНЫ. Каждая площадка щупается на четыре стены, о которые
     мы уже разбивались вживую:
        нужен ли аккаунт      (создание аккаунтов агентам запрещено)
        нужно ли подтверждение личности (тем более запрещено)
        нужны ли вложения     (бюджет ноль, иначе доказательство недействительно)
        доходят ли деньги     (omi платит PayPal — работа была бы выброшена)
     Стена — это не мнение, а найденное на странице слово. Не нашли — пишем
     «неизвестно», а не «всё в порядке».
  4. ОЦЕНКА. Путь без стен и с быстрым первым долларом стоит выше пути с
     большой суммой, до которой не дотянуться.

Чего разведчик НЕ делает: не решает, куда идти. Выбор пути — суждение, оно
уходит совету. Разведчик приносит проверенные варианты и честно говорит,
какие из них закрыты и чем именно.
"""
import sys, re, json, urllib.request, urllib.error
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.db import connect, ensure_schema
from core import guard, bus

ROOT = Path(__file__).resolve().parent.parent
UA = {"User-Agent": "Mozilla/5.0 (compatible; P0-prospector/1.0)",
      "Accept": "text/html,application/json"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS money_paths (
  id INTEGER PRIMARY KEY,
  platform TEXT NOT NULL UNIQUE,     -- домен площадки
  category TEXT NOT NULL,            -- класс заработка
  title TEXT,
  source_id INTEGER,                 -- откуда узнали; без источника путь не считается
  needs_account INTEGER,             -- 1 да, 0 нет, NULL не выяснили
  needs_kyc INTEGER,
  needs_money INTEGER,
  payout TEXT,                       -- crypto | fiat | unknown
  wall TEXT,                         -- главная стена, если путь закрыт
  open_to_us INTEGER,                -- 1 путь открыт, 0 закрыт, NULL неизвестно
  score REAL,
  evidence TEXT,                     -- дословные куски страницы, на которых стоит вывод
  checked_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS path_categories (
  id INTEGER PRIMARY KEY,
  category TEXT NOT NULL UNIQUE,
  searched_at TEXT,
  found INTEGER DEFAULT 0,
  open_found INTEGER DEFAULT 0
);
"""

# ПРОСТРАНСТВО ПОИСКА, а не список ответов. Каждый класс — гипотеза, которую
# ещё предстоит проверить живым поиском. Классы подобраны по признаку «здесь
# платят за результат, а не за присутствие», потому что присутствовать где-то
# круглосуточно мы можем, а быть человеком — нет.
CATEGORIES = {
    "баунти за код": ["open source bounty platform pay contributors",
                      "github issue bounty crypto payout"],
    "баунти за безопасность": ["bug bounty platform pays crypto researchers",
                               "smart contract audit bounty payout usdc"],
    "конкурсы и хакатоны": ["online hackathon prize crypto payout remote",
                            "ai agent competition prize money open"],
    "гранты": ["retroactive public goods funding grant open source",
               "developer grant program crypto no equity"],
    "рынок агентов и инструментов": ["agent marketplace developers earn per call",
                                     "mcp server marketplace paid tools"],
    "микроплатежи за вызовы": ["x402 monetize api agents pay per request",
                               "pay per call api marketplace crypto"],
    "продажа данных": ["sell dataset api developers marketplace",
                       "open data product paid api buyers"],
    "спонсорство сопровождения": ["open source maintainer sponsorship crypto",
                                  "github sponsors alternative crypto payout"],
    "краудсорсинг проверки": ["paid crowdsource testing bug reports remote",
                              "data labeling pays crypto no id verification"],
    "перевод и локализация": ["open source translation bounty paid",
                              "localization contribution reward program"],
    "партнёрские отчисления": ["developer affiliate program pays usdc",
                               "referral program crypto payout no kyc"],
    "предсказания и рынки": ["prediction market api rewards accuracy",
                             "forecasting competition prize payout"],
}

# СТЕНЫ. Слова, по которым площадка сама себя выдаёт. Ловим не догадкой,
# а буквальным совпадением, и рядом храним кусок текста как доказательство.
WALL_WORDS = {
    "needs_kyc": ("kyc", "verify your identity", "identity verification",
                  "government-issued id", "passport", "проверка личности"),
    "needs_account": ("sign up", "create an account", "register", "log in to",
                      "войти", "зарегистрируйтесь"),
    "needs_money": ("subscription", "pricing", "per month", "upgrade to",
                    "deposit", "stake ", "listing fee"),
}
PAYOUT_CRYPTO = ("usdc", "usdt", "crypto", "onchain", "on-chain", "wallet address",
                 "ethereum", "solana", "bitcoin", "stablecoin")
PAYOUT_FIAT = ("paypal", "bank transfer", "wire transfer", "stripe payout",
               "direct deposit", "venmo", "ach transfer")

# ПЛАТИТ ЛИ ОНА ИСПОЛНИТЕЛЮ. Без этой проверки оценка ловилась на пустом:
# coinmarketcap.com и dev.to получили высший балл только потому, что на их
# страницах попадаются слова про крипту. Первый — витрина котировок, второй —
# блог-платформа; работу там никто не оплачивает. Упоминание криптовалюты
# говорит о теме страницы, а не о том, что деньги идут В НАШУ сторону.
PAYS_OUT = ("bounty", "bounties", "reward", "rewards", "payout", "payouts",
            "you earn", "earn up to", "get paid", "we pay", "prize", "prizes",
            "grant", "grants", "compensation", "paid to contributors",
            "submit a report", "claim your")


def now():
    return datetime.now(timezone.utc).isoformat()


def _con():
    c = connect()
    ensure_schema(c, SCHEMA)
    return c


def _get(url, limit=120000, timeout=20):
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout)
        return r.read(limit).decode("utf-8", "ignore")
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _text(html):
    t = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", html or "")
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", t)


def _domain(url):
    m = re.match(r"https?://([^/:?#]+)", url or "")
    return m.group(1).lower().removeprefix("www.") if m else None


# Площадки, которые нам НЕ нужны: агрегаторы новостей, соцсети и наш же стек.
SKIP_DOMAINS = ("reddit.com", "medium.com", "youtube.com", "x.com", "twitter.com",
                "facebook.com", "linkedin.com", "quora.com", "pinterest.com",
                "wikipedia.org", "github.com", "stackoverflow.com", "news.ycombinator.com")


# ---------------------------------------------------------------- 1. разведка
def discover(categories=2, per_query=6):
    """Живой поиск площадок по нескольким классам за заход.

    Классы берутся по очереди — те, что дольше всех не проверялись. Так за
    сутки обходится всё пространство, и ни один класс не выпадает навсегда
    из-за того, что первый заход по нему был неудачным.
    """
    guard.check_action("research", "GREEN")
    from agents import scout

    con = _con()
    for cat in CATEGORIES:
        con.execute("INSERT OR IGNORE INTO path_categories(category) VALUES (?)", (cat,))
    con.commit()
    due = [r[0] for r in con.execute(
        "SELECT category FROM path_categories ORDER BY COALESCE(searched_at,'') LIMIT ?",
        (categories,)).fetchall()]
    con.close()

    added = 0
    for cat in due:
        answered = False          # хоть один запрос по классу реально отработал
        for query in CATEGORIES[cat]:
            hits = scout.search(query, limit=per_query)
            if hits and hits[0].get("error"):
                bus.broadcast("prospector", f"Поиск по классу «{cat}» не выполнен: "
                                            f"{hits[0]['error'][:80]}. Это не «путей нет».")
                continue
            if not hits:
                # Пустой ответ поисковика — это НЕ «в этом классе денег нет».
                # Ровно эту ошибку мы уже ловили у охотника за баунти: молчание
                # источника выдавалось за отсутствие работы. Класс остаётся
                # неразведанным и вернётся в очередь, а не считается пройденным.
                continue
            answered = True
            for h in hits:
                d = _domain(h.get("url"))
                if not d or any(s in d for s in SKIP_DOMAINS):
                    continue
                con = _con()
                exists = con.execute("SELECT 1 FROM money_paths WHERE platform=?", (d,)).fetchone()
                if exists:
                    con.close()
                    continue
                # источник обязателен: без строки в sources путь не считается найденным
                sid = con.execute(
                    "INSERT INTO sources(url,title,fetched_at,raw_excerpt) VALUES (?,?,?,?)",
                    (h["url"], h.get("title", "")[:200], now(),
                     f"поиск «{query}» по классу «{cat}»")).lastrowid
                con.execute("""INSERT INTO money_paths(platform,category,title,source_id,
                               checked_at) VALUES (?,?,?,?,?)""",
                            (d, cat, h.get("title", "")[:200], sid, now()))
                con.commit(); con.close()
                added += 1
        con = _con()
        n = con.execute("SELECT COUNT(*) FROM money_paths WHERE category=?", (cat,)).fetchone()[0]
        if answered:
            con.execute("UPDATE path_categories SET searched_at=?, found=? WHERE category=?",
                        (now(), n, cat))
        else:
            con.execute("UPDATE path_categories SET found=? WHERE category=?", (n, cat))
        con.commit(); con.close()
        if not answered:
            bus.broadcast("prospector", f"Класс «{cat}» остался неразведанным: поисковик не "
                                        f"дал ни одного ответа. Возвращаю его в очередь — "
                                        f"пустой ответ не значит, что там нет денег.")

    return f"разведаны классы: {', '.join(due)}; новых площадок {added}"


# ---------------------------------------------------------------- 2. проверка о стены
def probe(limit=6):
    """Щупает найденные площадки о четыре стены. Молчание = «неизвестно».

    Ни одна стена не ставится по догадке: рядом с выводом сохраняется кусок
    страницы, на котором он стоит. Если страница не открылась — путь остаётся
    непроверенным, а не объявляется открытым.
    """
    guard.check_action("research", "GREEN")
    con = _con()
    rows = con.execute("SELECT platform,category FROM money_paths "
                       "WHERE open_to_us IS NULL ORDER BY id LIMIT ?", (limit,)).fetchall()
    con.close()
    if not rows:
        return "непроверенных площадок нет"

    checked = opened = 0
    for platform, cat in rows:
        html = _get("https://" + platform)
        if not html:
            continue
        text = _text(html).lower()
        evidence, flags = [], {}
        for field, words in WALL_WORDS.items():
            hit = next((w for w in words if w in text), None)
            flags[field] = 1 if hit else 0
            if hit:
                i = text.find(hit)
                evidence.append(f"{field}: …{text[max(0, i - 60):i + 90]}…")

        if any(w in text for w in PAYOUT_CRYPTO):
            payout = "crypto"
        elif any(w in text for w in PAYOUT_FIAT):
            payout = "fiat"
        else:
            payout = "unknown"

        # ПЕРВЫЙ ВОПРОС — платят ли здесь вообще исполнителю. Пока это не
        # подтверждено словами самой страницы, обсуждать способ выплаты
        # бессмысленно: платить может быть просто не за что.
        pay_hit = next((w for w in PAYS_OUT if w in text), None)
        if pay_hit:
            i = text.find(pay_hit)
            evidence.append(f"платит исполнителю: …{text[max(0, i - 60):i + 90]}…")

        # Стена — это то, что закрывает путь ПОЛНОСТЬЮ, а не осложняет его.
        # Аккаунт сам по себе стеной не считается: владелец может завести его
        # сам, и это уже случалось. Личность и деньги — стены настоящие.
        wall = None
        if not pay_hit:
            wall = "не подтверждено, что площадка вообще платит исполнителю"
        elif flags["needs_kyc"]:
            wall = "требует подтверждения личности — агентам запрещено"
        elif flags["needs_money"] and payout != "crypto":
            wall = "требует вложений, а выплата не в крипте"
        elif payout == "fiat":
            wall = "платит только фиатом — до кошелька не дойдёт"
        open_to_us = 0 if wall else (1 if payout == "crypto" else None)

        score = 0.0
        if open_to_us == 1:
            score = 10.0
            if not flags["needs_account"]:
                score += 5          # ни одного шага владельца — самый ценный случай
            if not flags["needs_money"]:
                score += 3

        con = _con()
        con.execute("""UPDATE money_paths SET needs_account=?, needs_kyc=?, needs_money=?,
                       payout=?, wall=?, open_to_us=?, score=?, evidence=?, checked_at=?
                       WHERE platform=?""",
                    (flags["needs_account"], flags["needs_kyc"], flags["needs_money"],
                     payout, wall, open_to_us, score, " || ".join(evidence)[:900],
                     now(), platform))
        con.commit(); con.close()
        checked += 1
        if open_to_us == 1:
            opened += 1

    if opened:
        con = _con()
        best = con.execute("SELECT platform,category,payout FROM money_paths "
                           "WHERE open_to_us=1 ORDER BY score DESC LIMIT 1").fetchone()
        con.close()
        bus.broadcast("prospector", f"Проверено площадок: {checked}, открытых для нас "
                                    f"{opened}. Лучшая — {best[0]} (класс «{best[1]}», "
                                    f"платит {best[2]}). Это проверено по её же странице, "
                                    f"а не предположено.")
    return f"проверено {checked}, открытых {opened}"


# ---------------------------------------------------------------- 3. картина целиком
def landscape():
    """Что мы знаем о путях к деньгам прямо сейчас. Только измеренное."""
    con = _con()
    cats = con.execute("""SELECT category,
                          COUNT(*) n,
                          SUM(CASE WHEN open_to_us=1 THEN 1 ELSE 0 END) open,
                          SUM(CASE WHEN open_to_us=0 THEN 1 ELSE 0 END) closed,
                          SUM(CASE WHEN open_to_us IS NULL THEN 1 ELSE 0 END) unknown
                          FROM money_paths GROUP BY category ORDER BY open DESC, n DESC""").fetchall()
    top = con.execute("""SELECT platform,category,payout,score FROM money_paths
                         WHERE open_to_us=1 ORDER BY score DESC LIMIT 8""").fetchall()
    walls = con.execute("""SELECT wall,COUNT(*) n FROM money_paths WHERE wall IS NOT NULL
                           GROUP BY wall ORDER BY n DESC""").fetchall()
    untouched = con.execute("SELECT COUNT(*) FROM path_categories "
                            "WHERE searched_at IS NULL").fetchone()[0]
    con.close()
    return {
        "categories": [dict(zip(("category", "found", "open", "closed", "unknown"), r))
                       for r in cats],
        "open_paths": [dict(zip(("platform", "category", "payout", "score"), r)) for r in top],
        "walls": [dict(zip(("wall", "count"), r)) for r in walls],
        "categories_never_searched": untouched,
        "categories_total": len(CATEGORIES),
    }


def report():
    """Кладёт картину в чат и в доказательства. Выбор пути — не сюда, а в совет."""
    L = landscape()
    opened = sum(c["open"] or 0 for c in L["categories"])
    closed = sum(c["closed"] or 0 for c in L["categories"])
    found = sum(c["found"] or 0 for c in L["categories"])
    if not found:
        bus.broadcast("prospector", "Пространство путей ещё не размечено — разведка "
                                    "только началась, выводов не делаю.")
        return "путей пока не найдено"

    if L["open_paths"]:
        names = ", ".join(f"{p['platform']} ({p['category']})" for p in L["open_paths"][:3])
        bus.broadcast("prospector", f"Классов заработка в работе {L['categories_total']}, "
                                    f"площадок найдено {found}: открыто {opened}, "
                                    f"закрыто {closed}. Открытые: {names}. "
                                    f"Куда идти — решает совет, я приношу проверенное.")
    else:
        top_wall = L["walls"][0]["wall"] if L["walls"] else "причина не установлена"
        bus.broadcast("prospector", f"Площадок найдено {found}, открытых для нас НОЛЬ. "
                                    f"Чаще всего мешает: {top_wall}. Это измерение, "
                                    f"а не отговорка — продолжаю обход остальных классов.")
    return (f"классов {L['categories_total']}, площадок {found}, открытых {opened}, "
            f"закрытых {closed}, не размечено классов {L['categories_never_searched']}")


CYCLE = [("prospect", lambda: discover(2, 6)),
         ("probe_paths", lambda: probe(6)),
         ("path_report", report)]


if __name__ == "__main__":
    print(discover(3, 6))
    print(probe(10))
    print(report())
    print()
    L = landscape()
    print(f"{'КЛАСС':32} {'НАЙДЕНО':>8} {'ОТКРЫТО':>8} {'ЗАКРЫТО':>8} {'НЕЯСНО':>7}")
    for c in L["categories"]:
        print(f"{c['category'][:32]:32} {c['found']:>8} {c['open'] or 0:>8} "
              f"{c['closed'] or 0:>8} {c['unknown'] or 0:>7}")
    if L["walls"]:
        print("\nЧТО ЗАКРЫВАЕТ ПУТИ:")
        for w in L["walls"]:
            print(f"  {w['count']:>3}×  {w['wall']}")
