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
import sys, json, re, time, sqlite3, urllib.request, urllib.error
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.db import connect, ensure_schema
from core import guard, bus

ROOT = Path(__file__).resolve().parent.parent
IDX = ROOT / "data" / "bazaar_index.json"          # полный индекс (может отставать)
SLIM = ROOT / "worker" / "catalog.slim.json"      # свежий срез, его обновляет refresh_market

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
  id INTEGER PRIMARY KEY,
  domain TEXT NOT NULL UNIQUE,
  services INTEGER NOT NULL,
  calls_30d INTEGER NOT NULL,
  payers_30d INTEGER NOT NULL,     -- ВЕРХНЯЯ граница: сумма payer-set по ресурсам (кошелёк считается на каждом)
  payers_min_30d INTEGER,          -- НИЖНЯЯ граница: максимум по одному ресурсу
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
    # Миграция для уже существующей базы: CREATE TABLE IF NOT EXISTS колонку не добавит.
    cols = {r[1] for r in c.execute("PRAGMA table_info(leads)")}
    if "payers_min_30d" not in cols:
        c.execute("ALTER TABLE leads ADD COLUMN payers_min_30d INTEGER")
        c.commit()
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
def _records():
    """Нормализованные записи каталога: host, calls, payers, price, tags.

    Источник — СВЕЖИЙ срез catalog.slim.json (его обновляет refresh_market
    каждый цикл). Прежде лиды читали data/bazaar_index.json, который обновлялся
    отдельно и отставал на дни: каталог рос до 15 тысяч, а лиды разбирали
    трёхдневный снимок и не находили ни одной новой компании. Полный индекс
    оставлен запасным на случай, если свежего среза нет.
    """
    import json as _json
    if SLIM.exists():
        try:
            for r in _json.loads(SLIM.read_text(encoding="utf-8")):
                u = r.get("u") or ""
                host = u.split("//")[-1].split("/")[0] if "//" in u else ""
                tags = r.get("t")
                if isinstance(tags, str):
                    try:
                        tags = _json.loads(tags)
                    except ValueError:
                        tags = []
                yield {"host": host, "calls": int(r.get("c") or 0),
                       "payers": int(r.get("y") or 0),
                       "price": float(r.get("p") or 0) if r.get("p") else None,
                       "tags": tags or []}
            return
        except ValueError:
            pass
    for it in _index():
        u = it.get("resource") or ""
        host = u.split("//")[-1].split("/")[0] if "//" in u else ""
        a = (it.get("accepts") or [{}])[0]
        q = it.get("quality") or {}
        try:
            price = int(a.get("maxAmountRequired") or a.get("amount")) / 1e6
        except Exception:
            price = None
        yield {"host": host, "calls": q.get("l30DaysTotalCalls") or 0,
               "payers": q.get("l30DaysUniquePayers") or 0,
               "price": price, "tags": it.get("tags") or []}


def operators():
    """Сводит сервисы к компаниям. Один оператор часто держит десятки эндпоинтов —
    и чем их больше, тем серьёзнее его вложения. Источник — свежий каталог."""
    guard.check_action("research", "GREEN")
    ops = {}
    for r in _records():
        d = r["host"]
        if not d or any(x in d for x in SKIP):
            continue
        o = ops.setdefault(d, {"services": 0, "calls": 0, "payers": 0, "payers_max": 0,
                               "prices": [], "tags": {}})
        o["services"] += 1
        o["calls"] += r["calls"]
        # Вызовы аддитивны — сумма честна. Плательщики — НЕТ: один кошелёк,
        # вызвавший десять инструментов оператора, входит в payer-set каждого и
        # в сумме считается десять раз. Мейнтейнер agent402.tools поймал это
        # на нашем письме (мы назвали 701 при его реальных 158 за месяц).
        # Сумма — верхняя граница, максимум по одному ресурсу — нижняя; настоящее
        # число из каталога не выводится (только по цепочке: distinct senders на payTo).
        o["payers"] += r["payers"]
        o["payers_max"] = max(o["payers_max"], r["payers"])
        if r["price"] is not None:
            o["prices"].append(r["price"])
        for t in r["tags"]:
            o["tags"][t] = o["tags"].get(t, 0) + 1
    return ops


# ---------------------------------------------------------------- лиды
def hot_leads(limit=120):
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
        # Сигнал считается по НИЖНЕЙ границе плательщиков: сумма растёт с шириной
        # каталога (600 эндпоинтов — и один кошелёк считается 600 раз), и широкие
        # операторы всплывали в топ мимо одноэндпоинтных с настоящими покупателями.
        # Ширина каталога — тоже в логарифме: линейный множитель ставил оператора с
        # 820 эндпоинтами и ДВУМЯ плательщиками выше оператора с 33 эндпоинтами и
        # тысячей — ровно та инфляция «широких» продавцов, о которой писал agent402.
        signal = (math.log10(1 + o["services"]) * math.log10(1 + o["calls"])
                  * math.log10(1 + o["payers_max"]))
        avg = round(sum(o["prices"]) / len(o["prices"]), 4) if o["prices"] else None
        tags = ", ".join(t for t, _ in sorted(o["tags"].items(), key=lambda kv: -kv[1])[:3])
        rows.append({"domain": d, "services": o["services"], "calls_30d": o["calls"],
                     "payers_30d": o["payers"], "payers_min_30d": o["payers_max"],
                     "avg_price": avg, "top_tags": tags, "spend_signal": round(signal, 1)})
    rows.sort(key=lambda r: -r["spend_signal"])
    top = rows[:limit]
    # СИЛЬНЫЕ ПЛАТЕЛЬЩИКИ, КОТОРЫХ ФОРМУЛА УПУСКАЕТ. spend_signal умножает на
    # число сервисов, поэтому компания с ОДНИМ эндпоинтом, но сотнями плательщиков
    # (реальный покупатель) не попадала в топ. Добираем всех с заметным числом
    # плательщиков отдельно — это живые бизнесы, а не эксперименты.
    have = {r["domain"] for r in top}
    # ВСЕ ПЛАТЯЩИЕ ОПЕРАТОРЫ — лиды. rows уже отфильтрованы по payers>=5 (это
    # бизнес, а не эксперимент). Топ по spend_signal идёт первым, остальные —
    # следом; искусственного потолка нет, детектор согласованности следит,
    # чтобы число лидов не отставало от доступного в каталоге.
    extra = [r for r in rows if r["domain"] not in have]
    top = top + extra
    c = _con()
    for r in top:
        c.execute("""INSERT INTO leads(domain,services,calls_30d,payers_30d,payers_min_30d,
                     avg_price,top_tags,spend_signal,found_at) VALUES (?,?,?,?,?,?,?,?,?)
                     ON CONFLICT(domain) DO UPDATE SET services=?, calls_30d=?, payers_30d=?,
                     payers_min_30d=?, avg_price=?, top_tags=?, spend_signal=?""",
                  (r["domain"], r["services"], r["calls_30d"], r["payers_30d"],
                   r["payers_min_30d"], r["avg_price"], r["top_tags"], r["spend_signal"], now(),
                   r["services"], r["calls_30d"], r["payers_30d"], r["payers_min_30d"],
                   r["avg_price"], r["top_tags"], r["spend_signal"]))
    c.commit()
    # Кого в свежем каталоге больше нет — тот не лид, а память о нём: сигнал в ноль,
    # иначе старые строки со старой шкалой сигнала торчат в топе поверх живых.
    c.execute("CREATE TEMP TABLE IF NOT EXISTS fresh_domains(domain TEXT PRIMARY KEY)")
    c.execute("DELETE FROM fresh_domains")
    c.executemany("INSERT OR IGNORE INTO fresh_domains(domain) VALUES (?)",
                  [(r["domain"],) for r in top])
    c.execute("UPDATE leads SET spend_signal=0 WHERE domain NOT IN (SELECT domain FROM fresh_domains)")
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
def _brand(domain):
    """Опорное слово бренда из домена: blockrun.ai -> blockrun, api.nansen.ai -> nansen."""
    parts = [x for x in domain.lower().split(".") if x not in
             ("api", "app", "www", "io", "ai", "com", "org", "net", "dev", "xyz",
              "co", "tech", "vercel", "online", "markets", "services")]
    return max(parts, key=len) if parts else domain.split(".")[0]


# Слова, которые «брендом» быть не могут: по ним поиск GitHub отдаёт чужие
# репозитории (x402.twit.sh → x402-foundation/x402 — репозиторий ФОНДА протокола,
# а не продавца). Написать туда — значит спамить не тому.
GENERIC_BRANDS = frozenset("""
x402 agent agents crypto base chain token tokens wallet data cloud node market pay
web3 defi bot bots tools labs protocol network finance exchange service services
search index stable coin swap bridge oracle mcp llm gpt
""".split())


def _domain_root(domain):
    """Регистрируемая часть: api.onesource.io -> onesource.io, x402.twit.sh -> twit.sh."""
    parts = domain.lower().split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else domain.lower()


def _accept_repo(domain, brand, full_name):
    """Принадлежит ли репозиторий продавцу. Совпадения бренда в имени МАЛО —
    нужен признак владения: домен в homepage/описании или бренд в логине владельца.
    Плюс issues должны быть открыты (иначе писать некуда)."""
    import subprocess
    try:
        v = subprocess.run(["gh", "repo", "view", full_name, "--json",
                            "hasIssuesEnabled,homepageUrl,description,owner"],
                           capture_output=True, text=True, timeout=30,
                           encoding="utf-8", errors="ignore")
        if v.returncode != 0:
            return None            # НЕЗНАНИЕ, не отказ: сбой gh/лимит не должен отбирать канал
        info = json.loads(v.stdout or "{}")
    except (subprocess.SubprocessError, OSError, ValueError):
        return None
    if not info.get("hasIssuesEnabled"):
        return False
    root = _domain_root(domain)
    home = str(info.get("homepageUrl") or "").lower()
    desc = str(info.get("description") or "").lower()
    owner = str((info.get("owner") or {}).get("login") or "").lower()
    # ТОЛЬКО ДОМАШНЯЯ СТРАНИЦА ИЛИ ВЛАДЕЛЕЦ. Упоминание домена в описании принимало чужие
    # репозитории: 15.09 письмо laso.finance ушло в ranaroussi/yfinance («wtf are you talking
    # about?»), другое — в экспортёр CoinMarketCap, который бирже не принадлежит.
    del desc
    return (root in home) or (brand in owner)


def _github_channel(domain):
    """Публичный репозиторий бренда через поиск GitHub — не только ссылка на главной.

    Каналы для agent402 и blockrun нашлись именно так: их github-репозиториев не
    было на главной, но поиск по бренду их дал. Берём репозиторий, у которого
    имя содержит бренд И есть признак владения (см. _accept_repo), issues
    открыты, он не в архиве. Одного совпадения по бренду мало — по коротким и
    общим словам поиск приписывал чужие репозитории.
    """
    import subprocess
    brand = _brand(domain)
    if len(brand) < 4 or brand in GENERIC_BRANDS:
        return None
    try:
        r = subprocess.run(
            ["gh", "search", "repos", brand, "--limit", "10", "--json",
             "fullName,isArchived,stargazersCount"],
            capture_output=True, text=True, timeout=40, encoding="utf-8", errors="ignore")
    except (subprocess.SubprocessError, OSError):
        return None
    if r.returncode != 0:
        return None
    try:
        repos = json.loads(r.stdout or "[]")
    except ValueError:
        return None
    cand = [x for x in repos if not x.get("isArchived")
            and brand in x["fullName"].lower()]
    cand.sort(key=lambda x: -(x.get("stargazersCount") or 0))
    for x in cand:
        if _accept_repo(domain, brand, x["fullName"]):
            return x["fullName"]
    return None


def revalidate_channels(limit=200):
    """Перепроверка уже найденных каналов новым правилом владения: чужие — в reachable=0."""
    guard.check_action("research", "GREEN")
    con = _con()
    # Каналы, куда уже писали, подтверждены делом (их находила ссылка с сайта
    # продавца, а не поиск по бренду) — перепроверке по бренду не подлежат.
    con.execute("CREATE TABLE IF NOT EXISTS outreach (id INTEGER PRIMARY KEY, domain TEXT NOT NULL "
                "UNIQUE, channel TEXT, url TEXT, sent_at TEXT NOT NULL, note TEXT)")
    rows = con.execute("SELECT domain, channel FROM leads WHERE reachable=1 AND channel IS NOT NULL "
                       "AND domain NOT IN (SELECT domain FROM outreach) "
                       "ORDER BY spend_signal DESC LIMIT ?", (limit,)).fetchall()
    con.close()
    # Сначала вердикты (сеть, без базы), потом ОДНА короткая запись: живой воркер
    # держит базу занятой, и запись по одной строке ловит «database is locked».
    # Незнание (сбой gh) — НЕ отказ: канал остаётся, иначе лимит GitHub отбирал
    # бы настоящие каналы (так на минуту пропал blockrun.ai).
    kept, dropped = 0, []
    for domain, channel in rows:
        brand = _brand(domain)
        if len(brand) < 4 or brand in GENERIC_BRANDS or brand not in channel.lower():
            dropped.append((domain, channel))
            continue
        verdict = _accept_repo(domain, brand, channel)
        if verdict is False:
            dropped.append((domain, channel))
        else:
            kept += 1
    if dropped:
        import time as _time
        for attempt in range(6):
            try:
                con = _con()
                con.executemany("UPDATE leads SET reachable=0, note=? WHERE domain=?",
                                [(f"канал {ch} отклонён перепроверкой владения", d)
                                 for d, ch in dropped])
                con.commit(); con.close()
                break
            except sqlite3.OperationalError as e:
                if "locked" not in str(e).lower() or attempt == 5:
                    raise
                _time.sleep(2 + attempt)
    names = [f"{d}→{ch}" for d, ch in dropped]
    return f"перепроверено {len(rows)}: оставлено {kept}, отклонено {len(dropped)}" + \
           (f" ({', '.join(names[:8])}{'…' if len(names) > 8 else ''})" if names else "")


def find_channel(limit=25):
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
        if not repo:
            repo = _github_channel(domain)   # второй метод: поиск репозитория бренда
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
         # 10 за редкий шаг = ~60 лидов в сутки при 886 в очереди: 825 стояли без
         # проверки канала (находка сверки согласованности). 60 за шаг — это
         # 1–2 минуты поиска GitHub, в пределах его лимита 30 запросов/мин.
         ("find_channel", lambda: find_channel(60)),
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
