"""РАЗВЕДКА БЕСПЛАТНЫХ ИСТОЧНИКОВ — что реально отвечает без ключа и аккаунта.

Список составлен не по памяти: каждый адрес дёргается живым запросом, и в
итог попадает только то, что ответило. Источник, который «вроде бы есть»,
но требует ключ или молчит, честно помечается как непригодный.

Разделение по назначению важнее длины списка: нам нужны не «любые API», а
четыре вещи — где взять РАБОТУ, где взять ДАННЫЕ для продукта, где нас могут
НАЙТИ, и чем всё это обслуживать.
"""
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
      "Accept": "application/json, text/html"}

# назначение -> [(имя, адрес, что ищем в ответе)]
CANDIDATES = {
    "РАБОТА: где платят за результат": [
        ("Hacker News «кто нанимает»",
         "https://hn.algolia.com/api/v1/search?query=freelancer%20seeking%20work&tags=story&hitsPerPage=3",
         "hits"),
        ("Hacker News свежие вакансии",
         "https://hacker-news.firebaseio.com/v0/jobstories.json", None),
        ("Devpost: хакатоны с призами",
         "https://devpost.com/api/hackathons?status[]=open&order_by=prize-amount", "hackathons"),
        ("Kaggle: соревнования",
         "https://www.kaggle.com/api/v1/competitions/list", None),
        ("GitHub: задачи с меткой награды",
         "https://api.github.com/search/issues?q=label:bounty+state:open&per_page=1", "items"),
    ],
    "ДАННЫЕ: сырьё для продукта": [
        ("DefiLlama: протоколы и TVL",
         "https://api.llama.fi/protocols", None),
        ("CoinGecko: рынок монет",
         "https://api.coingecko.com/api/v3/simple/price?ids=ethereum&vs_currencies=usd", "ethereum"),
        ("Blockscout Base: кошелёк",
         "https://base.blockscout.com/api/v2/stats", None),
        ("Ethereum RPC (публичный)",
         "https://mainnet.base.org", None),
        ("PyPI: сведения о пакете",
         "https://pypi.org/pypi/requests/json", "info"),
        ("npm: сведения о пакете",
         "https://registry.npmjs.org/express", "name"),
        ("crates.io: пакеты Rust",
         "https://crates.io/api/v1/crates?page=1&per_page=1", "crates"),
        ("Open Collective: открытое финансирование",
         "https://api.opencollective.com/graphql/v2", None),
    ],
    "ВИДИМОСТЬ: где нас могут найти": [
        ("Реестр MCP",
         "https://registry.modelcontextprotocol.io/v0/servers?limit=1", "servers"),
        ("Индекс Bazaar (чтение без ключа)",
         "https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources?limit=1", "items"),
        ("Индекс OpenFacilitator",
         "https://pay.openfacilitator.io/discovery/resources", None),
        ("Каталог пользовательских скриптов",
         "https://greasyfork.org/en/scripts.json?page=1", None),
    ],
    "ОБСЛУЖИВАНИЕ: чем это всё крутить": [
        ("Cloudflare API (наш токен)",
         "https://api.cloudflare.com/client/v4/user/tokens/verify", "success"),
        ("GitHub API",
         "https://api.github.com/rate_limit", "resources"),
        ("Локальные модели",
         "http://127.0.0.1:11434/api/tags", "models"),
    ],
}


def probe(url, want, timeout=22):
    """Возвращает (код, есть_ли_нужное, короткая_подпись)."""
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout)
        raw = r.read(120000).decode("utf-8", "ignore")
        code = r.status
    except urllib.error.HTTPError as e:
        return e.code, False, f"отказ {e.code}"
    except Exception as e:
        return 0, False, type(e).__name__

    if want is None:
        return code, bool(raw.strip()), f"{len(raw)} байт"
    try:
        d = json.loads(raw)
    except ValueError:
        return code, want in raw, "не JSON"
    if isinstance(d, dict):
        ok = want in d
        n = len(d.get(want) or []) if ok and isinstance(d.get(want), list) else None
        return code, ok, (f"{n} записей" if n is not None else "поле есть" if ok else "поля нет")
    return code, isinstance(d, list) and bool(d), f"{len(d)} записей" if isinstance(d, list) else "?"


def main():
    usable = []
    print("=" * 76)
    print("БЕСПЛАТНЫЕ ИСТОЧНИКИ — проверка живыми запросами")
    print("=" * 76)
    for group, items in CANDIDATES.items():
        print(f"\n── {group}")
        for name, url, want in items:
            code, ok, note = probe(url, want)
            mark = "ГОДЕН " if (code == 200 and ok) else "нет   "
            print(f"  {mark} {name:<36} {code:>3}  {note}")
            if code == 200 and ok:
                usable.append({"group": group, "name": name, "url": url})
            time.sleep(.4)
    print("\n" + "=" * 76)
    print(f"ПРИГОДНЫХ ИСТОЧНИКОВ: {len(usable)}")
    out = ROOT / "data" / "free_sources.json"
    out.write_text(json.dumps(usable, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"записано: {out}")
    return usable


if __name__ == "__main__":
    main()
