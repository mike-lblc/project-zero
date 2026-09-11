"""ПОДКЛЮЧЕНИЯ К БЕСПЛАТНЫМ ИСТОЧНИКАМ — по одному на каждый проверенный.

Каждая функция здесь — это связь с конкретным источником, который ОТВЕТИЛ на
живой запрос при проверке. Ни одного адреса «по памяти»: список собран
ops/probe_free.py, и то, что требует ключа или молчит, сюда не попало.

Разделение по назначению важнее количества. Нам нужны четыре вещи:

    РАБОТА      где платят за результат
    ДАННЫЕ      сырьё, из которого делается наш товар
    ВИДИМОСТЬ   места, где нас могут найти
    СЛУЖБА      чем всё это крутится

Что сюда НЕ попало и почему — записано рядом с каждым отказом. Источник,
отвергнутый молча, через неделю кажется недосмотром; отвергнутый с причиной
экономит повторную проверку.

ОБЩЕЕ ПРАВИЛО ДЛЯ ВСЕХ ФУНКЦИЙ: отказ источника возвращается как отказ, а не
как пустой результат. Мы уже теряли обход рынка на том, что молчание API
докладывалось как «работы нет».
"""
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
      "Accept": "application/json, text/html"}


import time as _time


def _measure(host, t0, ok, detail=None):
    """Сколько занял поход к источнику и чем кончился.

    Источник, который отвечает всё медленнее, умирает незаметно: он ещё не
    отказывает, но уже съедает цикл. Без замера это видно только тогда, когда
    он окончательно замолчит.
    """
    try:
        from core import telemetry
        telemetry.record("source", host, (_time.time() - t0) * 1000, ok, detail=detail)
    except Exception:
        pass


class SourceDown(Exception):
    """Источник не ответил. Это НЕ «там ничего нет»."""


def _get(url, timeout=25, raw=False, max_bytes=600_000, accept=None):
    """Читает ответ ЦЕЛИКОМ. Превышение потолка — отказ, а не обрезка.

    Так было не сразу: первая версия читала первые 400 КБ и отдавала огрызок
    дальше. Ответ DefiLlama весит 8.8 МБ, JSON ломался на середине, и источник
    докладывался как «не разобрался» — то есть исправная служба выглядела
    сломанной. Молчаливая обрезка данных — та же ложь, что молчаливый пустой
    список, поэтому потолок теперь поднимает отказ с внятной причиной.
    """
    host = url.split("/")[2]
    t0 = _time.time()
    try:
        head = dict(UA, **({"Accept": accept} if accept else {}))
        r = urllib.request.urlopen(urllib.request.Request(url, headers=head), timeout=timeout)
        blob = r.read(max_bytes + 1)
        if len(blob) > max_bytes:
            raise SourceDown(f"{url.split('/')[2]}: ответ больше {max_bytes // 1000} КБ, "
                             f"нужен потолок выше или адрес полегче")
        body = blob.decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        _measure(host, t0, False, f"отказ {e.code}")
        raise SourceDown(f"{host}: отказ {e.code}") from e
    except SourceDown as e:
        _measure(host, t0, False, str(e)[:90])
        raise                 # свой отказ уже назван причиной — не переименовывать
    except Exception as e:
        _measure(host, t0, False, type(e).__name__)
        raise SourceDown(f"{host}: {type(e).__name__}") from e
    _measure(host, t0, True)
    if raw:
        return body
    try:
        return json.loads(body)
    except ValueError as e:
        raise SourceDown(f"{host}: ответ не разобрался") from e


# ═════════════════════════════════════════════════ РАБОТА
def hackathons(min_prize=500, limit=12):
    """Открытые хакатоны с объявленным призовым фондом (Devpost, без ключа).

    ЧЕСТНО О ПРИРОДЕ ЭТИХ ДЕНЕГ: это конкурс, а не заказ. Платят одному
    победителю, остальные работают бесплатно. Поэтому призовой фонд здесь —
    масштаб события, а не наше ожидание, и оценивать его как баунти значит
    обманывать себя приятным образом.
    """
    d = _get("https://devpost.com/api/hackathons?status[]=open&order_by=prize-amount")
    out = []
    for h in (d.get("hackathons") or [])[:limit * 2]:
        prize = str(h.get("prize_amount") or "")
        digits = "".join(c for c in prize if c.isdigit())
        amount = int(digits) if digits else 0
        if amount < min_prize:
            continue
        out.append({
            "source": "devpost", "kind": "конкурс",
            "title": (h.get("title") or "")[:150],
            "url": h.get("url"),
            "prize_usd": amount,
            "deadline": h.get("submission_period_dates"),
            "themes": [t.get("name") for t in (h.get("themes") or [])][:4],
            "note": "конкурс: платят одному победителю",
        })
        if len(out) >= limit:
            break
    return out


def hn_jobs(limit=10):
    """Свежие вакансии и заказы с Hacker News. Без ключа, без аккаунта."""
    ids = _get("https://hacker-news.firebaseio.com/v0/jobstories.json")[:limit]
    out = []
    for i in ids:
        try:
            it = _get(f"https://hacker-news.firebaseio.com/v0/item/{i}.json")
        except SourceDown:
            continue
        if not it:
            continue
        out.append({"source": "hacker news", "kind": "вакансия",
                    "title": (it.get("title") or "")[:150],
                    "url": it.get("url") or f"https://news.ycombinator.com/item?id={i}",
                    "at": it.get("time")})
    return out


def hn_search(query, limit=10):
    """Поиск по обсуждениям Hacker News: кто ищет исполнителя и на что жалуется."""
    q = urllib.parse.quote(query)
    d = _get(f"https://hn.algolia.com/api/v1/search?query={q}&tags=story&hitsPerPage={limit}")
    return [{"source": "hacker news", "kind": "обсуждение",
             "title": (h.get("title") or "")[:150],
             "url": h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}",
             "points": h.get("points"), "comments": h.get("num_comments")}
            for h in (d.get("hits") or [])]


# ═════════════════════════════════════════════════ ДАННЫЕ
def defi_chains(limit=25):
    """Сети и объём средств в каждой. Лёгкий срез (64 КБ) — годится в цикл.

    Зачем нам: подтверждает, что Base, в которой нам платят, живая, и
    показывает соседние сети, где та же схема оплаты имела бы спрос.
    """
    d = _get("https://api.llama.fi/v2/chains", max_bytes=400_000)
    rows = sorted(d, key=lambda c: -(c.get("tvl") or 0))[:limit]
    return [{"source": "defillama", "chain": c.get("name"),
             "tvl_usd": round(c.get("tvl") or 0),
             "token": c.get("tokenSymbol")} for c in rows]


def defi_protocols(limit=40):
    """Протоколы DeFi с объёмом средств. Свободный источник без ключа.

    Зачем нам: это список компаний, у которых заведомо есть деньги и заведомо
    есть публичный продукт. Пересечение с нашим рынком даёт тех, кому наши
    данные могут быть нужны.

    Ответ весит около 9 МБ, поэтому он кладётся на диск на шесть часов: тянуть
    девять мегабайт каждый цикл — расход без новой информации.
    """
    cache = ROOT / "data" / "defi_protocols.json"
    fresh = cache.exists() and (time.time() - cache.stat().st_mtime) < 6 * 3600
    if fresh:
        d = json.loads(cache.read_text(encoding="utf-8"))
    else:
        d = _get("https://api.llama.fi/protocols", timeout=60, max_bytes=20_000_000)
        cache.parent.mkdir(exist_ok=True)
        cache.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    rows = sorted(d, key=lambda p: -(p.get("tvl") or 0))[:limit]
    return [{"source": "defillama", "name": p.get("name"),
             "tvl_usd": round(p.get("tvl") or 0),
             "chains": (p.get("chains") or [])[:4],
             "url": p.get("url"), "category": p.get("category")}
            for p in rows]


def coin_price(ids="ethereum,usd-coin"):
    """Курсы монет. Нужны, чтобы считать выручку в долларах, а не в токенах."""
    d = _get(f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd")
    return {k: v.get("usd") for k, v in d.items()}


def chain_stats():
    """Состояние сети Base — той, в которой нам платят."""
    d = _get("https://base.blockscout.com/api/v2/stats")
    return {"blocks": d.get("total_blocks"), "txs": d.get("total_transactions"),
            "addresses": d.get("total_addresses"), "gas": d.get("gas_prices")}


def package_info(name, registry="pypi"):
    """Сведения о пакете: свежесть, ссылки, документация.

    Зачем нам: пакет с живым трафиком и запущенной документацией — это готовая
    работа класса «документация», за который на баунти-площадках платят и
    качество которой проверяемо механически.
    """
    if registry == "pypi":
        d = _get(f"https://pypi.org/pypi/{urllib.parse.quote(name)}/json")
        i = d.get("info") or {}
        return {"registry": "pypi", "name": i.get("name"), "version": i.get("version"),
                "home": i.get("home_page") or i.get("project_url"),
                "docs": (i.get("project_urls") or {}).get("Documentation"),
                "summary": (i.get("summary") or "")[:160]}
    if registry == "npm":
        # Только манифест последней версии. Полный packument популярного пакета
        # весит мегабайты, а краткая форма отдаёт версии без ссылок на
        # репозиторий — то есть ровно без того, ради чего мы сюда ходим.
        v = _get(f"https://registry.npmjs.org/{urllib.parse.quote(name)}/latest")
        repo = v.get("repository")
        return {"registry": "npm", "name": v.get("name"), "version": v.get("version"),
                "home": v.get("homepage"),
                "repo": repo.get("url") if isinstance(repo, dict) else repo,
                "summary": (v.get("description") or "")[:160]}
    if registry == "crates":
        d = _get(f"https://crates.io/api/v1/crates/{urllib.parse.quote(name)}")
        c = d.get("crate") or {}
        return {"registry": "crates.io", "name": c.get("name"),
                "version": c.get("max_version"), "docs": c.get("documentation"),
                "repo": c.get("repository"), "downloads": c.get("downloads"),
                "summary": (c.get("description") or "")[:160]}
    raise ValueError(f"неизвестный реестр: {registry}")


# ═════════════════════════════════════════════════ ВИДИМОСТЬ
def visibility():
    """Видят ли нас там, где агенты ищут сервисы. Четыре независимых индекса.

    Отказ индекса и отсутствие в индексе — разные вещи. Мы уже три часа
    рапортовали «не опубликованы», когда реестр просто не отвечал на наш
    служебный User-Agent.
    """
    SELF = "x402-bazaar-rank"
    out = {}
    checks = [
        ("реестр MCP",
         f"https://registry.modelcontextprotocol.io/v0/servers?search={SELF}"),
        ("индекс Bazaar",
         "https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources?limit=100"),
        ("индекс OpenFacilitator",
         "https://pay.openfacilitator.io/discovery/resources"),
    ]
    for name, url in checks:
        try:
            body = _get(url, raw=True)
        except SourceDown as e:
            out[name] = {"listed": None, "why": str(e)}   # НЕ ЗНАЕМ, а не «нет»
            continue
        out[name] = {"listed": SELF in body, "why": "проверено по ответу индекса"}
    return out


def script_demand(limit=15):
    """Где люди дописывают недостающие функции руками — каталог Greasyfork.

    Что это и почему именно так. Сначала был взят адрес scripts.json, он
    ответил 200, и проверка засчитала источник годным. Но внутри оказалось
    описание формы поиска, а не скрипты: «годен» по коду ответа и пустой
    список по сути — ровно тот обман, который мы ловим у себя. Рабочий срез —
    by-site: сайт и число написанных под него скриптов.

    Зачем нам: много самодельных скриптов под один сайт значит, что у людей
    там есть задача, которую продукт не решает. Это спрос на маленький
    инструмент, измеренный чужими руками, а не нашим предположением.
    """
    d = _get("https://greasyfork.org/en/scripts/by-site.json", max_bytes=2_000_000)
    rows = sorted(((k, v) for k, v in d.items() if k and isinstance(v, int)),
                  key=lambda kv: -kv[1])[:limit]
    return [{"source": "greasyfork", "site": site, "scripts": n,
             "signal": "чем больше самодельных скриптов, тем заметнее нехватка"}
            for site, n in rows]


# ═════════════════════════════════════════════════ СЛУЖБА
def github_budget():
    """Сколько запросов к GitHub у нас осталось. Лимит — наш главный расходник."""
    d = _get("https://api.github.com/rate_limit")
    c = (d.get("resources") or {}).get("core") or {}
    s = (d.get("resources") or {}).get("search") or {}
    return {"core_left": c.get("remaining"), "core_limit": c.get("limit"),
            "search_left": s.get("remaining"), "search_limit": s.get("limit")}


def local_models():
    """Какие модели доступны локально. Бесплатный разум системы."""
    d = _get("http://127.0.0.1:11434/api/tags", timeout=15)
    return [{"name": m.get("name"), "gb": round((m.get("size") or 0)/1e9, 1)}
            for m in (d.get("models") or [])]


# ═════════════════════════════════════════════════ ЧТО ОТВЕРГНУТО И ПОЧЕМУ
# Записано, чтобы через неделю это не выглядело недосмотром и чтобы никто
# не тратил время на повторную проверку.
REJECTED = {
    "Kaggle": "отвечает 401 — нужен аккаунт и токен, соревнования читаются "
              "только авторизованно",
    "публичный RPC Base": "отвечает 405 на GET: это JSON-RPC, работает только "
                          "POST-ом; нам он сейчас не нужен — состояние сети "
                          "берётся из Blockscout",
    "Open Collective": "GraphQL, отвечает 400 на GET; подключение возможно, но "
                       "пользы для миссии не видно: там ищут спонсоров, а не "
                       "исполнителей",
}


def inventory():
    """Все подключения и их состояние прямо сейчас. Проверка, а не декларация."""
    checks = {
        "хакатоны с призами": lambda: len(hackathons(limit=3)),
        "вакансии Hacker News": lambda: len(hn_jobs(limit=3)),
        "протоколы DeFi": lambda: len(defi_protocols(limit=5)),
        "курсы монет": lambda: len(coin_price()),
        "состояние сети Base": lambda: bool(chain_stats().get("blocks")),
        "реестр пакетов": lambda: bool(package_info("requests").get("version")),
        "видимость в индексах": lambda: len(visibility()),
        "сети и объём средств": lambda: len(defi_chains(5)),
        "спрос на мелкие инструменты": lambda: len(script_demand(5)),
        "бюджет запросов GitHub": lambda: github_budget().get("core_left"),
        "локальные модели": lambda: len(local_models()),
    }
    out = {}
    for name, fn in checks.items():
        try:
            out[name] = {"ok": True, "value": fn()}
        except Exception as e:
            out[name] = {"ok": False, "why": f"{type(e).__name__}: {str(e)[:80]}"}
    return out


if __name__ == "__main__":
    print("=" * 72)
    print("ПОДКЛЮЧЕНИЯ К БЕСПЛАТНЫМ ИСТОЧНИКАМ")
    print("=" * 72)
    inv = inventory()
    for name, r in inv.items():
        print(f"  {'ok  ' if r['ok'] else 'СБОЙ'} {name:<28} "
              f"{r.get('value') if r['ok'] else r.get('why')}")
    live = sum(1 for r in inv.values() if r["ok"])
    print(f"\nживых подключений: {live} из {len(inv)}")
    print("\nОТВЕРГНУТО И ПОЧЕМУ:")
    for k, v in REJECTED.items():
        print(f"  {k}: {v}")
