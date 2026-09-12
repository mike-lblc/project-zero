"""НАБЛЮДЕНИЕ ЗА ПОСТУПЛЕНИЯМИ ПО ВСЕМ МАРШРУТАМ, А НЕ ПО ОДНОМУ.

До этого модуля система смотрела ровно один адрес в одной сети и записывала
поступления с жёстко зашитым `chain='base'`. Работа, платящая иначе, не то
чтобы отклонялась — её просто некуда было принять, и это молчаливо сужало
рынок сильнее любого фильтра.

ЧТО ЗДЕСЬ ЕСТЬ. Обход всех маршрутов, которые объявлены в реестре и проверены
живым запросом: несколько сетей EVM и биткоин. Каждая сеть опрашивается своим
обозревателем, потому что общего бесплатного не существует, и каждый ответ
разбирается отдельно — у них разные поля для одного и того же.

ЧЕГО ЗДЕСЬ НЕТ. Ни одной функции, способной перевести, обменять или потратить.
Наблюдение — это чтение; право получать и право распоряжаться разные, и
второго у системы нет.

ПРОВЕРКА МАРШРУТА — НЕ ФОРМАЛЬНОСТЬ. Маршрут считается рабочим только после
того, как его обозреватель ответил на запрос о нашем адресе. Объявить маршрут
рабочим по факту существования адреса значит принять обещание за факт: адрес
один, а сетей много, и в каждой он живёт по своим правилам.
"""
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import payment  # noqa: E402

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
      "Accept": "application/json"}

# Бесплатные обозреватели без ключа. Ключ означал бы аккаунт, а аккаунты
# агентам заводить запрещено.
EXPLORERS = {
    "base": "https://base.blockscout.com/api/v2/addresses/{addr}",
    "ethereum": "https://eth.blockscout.com/api/v2/addresses/{addr}",
    "polygon": "https://polygon.blockscout.com/api/v2/addresses/{addr}",
    "arbitrum": "https://arbitrum.blockscout.com/api/v2/addresses/{addr}",
    "bitcoin": "https://blockstream.info/api/address/{addr}",
}

TRANSFERS = {
    "base": "https://base.blockscout.com/api/v2/addresses/{addr}/token-transfers?type=ERC-20",
    "ethereum": "https://eth.blockscout.com/api/v2/addresses/{addr}/token-transfers?type=ERC-20",
    "polygon": "https://polygon.blockscout.com/api/v2/addresses/{addr}/token-transfers?type=ERC-20",
    "arbitrum": "https://arbitrum.blockscout.com/api/v2/addresses/{addr}/token-transfers?type=ERC-20",
    "bitcoin": "https://blockstream.info/api/address/{addr}/txs",
}


def _get(url, timeout=25):
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout)
        return json.loads(r.read(1_500_000).decode("utf-8", "ignore"))
    except urllib.error.HTTPError as e:
        return {"__error__": f"отказ {e.code}"}
    except Exception as e:
        return {"__error__": type(e).__name__}


def verify_routes():
    """Проверяет маршруты живым запросом и записывает результат с датой.

    Проверяются только те, где есть куда смотреть — самостоятельное хранение.
    Площадки выплат (Algora, Polar и прочие) требуют аккаунта: их состояние
    определяется не запросом, а тем, заведён ли аккаунт владельцем, и решать
    это не агентам.
    """
    out = []
    for net, tpl in EXPLORERS.items():
        addr = (payment.OWNER_DESTINATIONS["btc"] if net == "bitcoin"
                else payment.OWNER_DESTINATIONS["evm"])
        d = _get(tpl.format(addr=addr))
        if d.get("__error__"):
            payment.mark("self-custody", network=net, status="unverified",
                         failure_reason=f"обозреватель не ответил: {d['__error__']}")
            out.append({"сеть": net, "итог": "не проверен", "почему": d["__error__"]})
            continue
        payment.mark("self-custody", network=net, status="verified",
                     account_ready=1, kyc_required=0, withdrawal_available=1,
                     minimum_payout=0.0, failure_reason=None)
        out.append({"сеть": net, "итог": "проверен",
                    "адрес": addr[:10] + "…" + addr[-6:]})
    return out


def _incoming_evm(net, addr):
    """Входящие переводы токенов в сети EVM. ТОЛЬКО входящие."""
    d = _get(TRANSFERS[net].format(addr=addr))
    if d.get("__error__"):
        return None, d["__error__"]
    found = []
    for t in (d.get("items") or []):
        to = ((t.get("to") or {}).get("hash") or "").lower()
        if to != addr.lower():
            continue          # исходящий перевод доходом не является
        h = t.get("transaction_hash") or t.get("tx_hash") or ""
        if not h:
            continue
        tok = t.get("token") or {}
        dec = int(tok.get("decimals") or 18)
        raw = (t.get("total") or {}).get("value") or t.get("value") or "0"
        try:
            amount = int(raw) / (10 ** dec)
        except (TypeError, ValueError):
            continue
        found.append({"proof": h, "amount": amount,
                      "currency": tok.get("symbol") or "?", "network": net,
                      "from": ((t.get("from") or {}).get("hash") or "")})
    return found, None


def _incoming_btc(addr):
    """Входящие переводы биткоина. Разбор другой: там нет токенов и адресатов."""
    d = _get(TRANSFERS["bitcoin"].format(addr=addr))
    if isinstance(d, dict) and d.get("__error__"):
        return None, d["__error__"]
    found = []
    for tx in (d if isinstance(d, list) else []):
        h = tx.get("txid")
        if not h:
            continue
        sats = sum(o.get("value", 0) for o in (tx.get("vout") or [])
                   if o.get("scriptpubkey_address") == addr)
        if sats <= 0:
            continue
        found.append({"proof": h, "amount": sats / 1e8, "currency": "BTC",
                      "network": "bitcoin", "from": ""})
    return found, None


def watch():
    """Обходит все ПРОВЕРЕННЫЕ маршруты и записывает новые поступления.

    Отказ обозревателя возвращается как отказ, а не как «денег нет»: это
    разные вещи, и мы уже теряли обход рынка, доложив молчание как пустоту.
    """
    verified = [r for r in payment.routes() if r["status"] == "verified"]
    if not verified:
        return "проверенных маршрутов нет — сперва проверка маршрутов"

    seen, failed, added = 0, [], 0
    for r in verified:
        net = r.get("network")
        if not net or net not in TRANSFERS:
            continue
        addr = (payment.OWNER_DESTINATIONS["btc"] if net == "bitcoin"
                else payment.OWNER_DESTINATIONS["evm"])
        items, err = (_incoming_btc(addr) if net == "bitcoin"
                      else _incoming_evm(net, addr))
        if err:
            failed.append(f"{net}: {err}")
            continue
        seen += len(items)
        for it in items:
            res = payment.record_receipt(
                proof=it["proof"], proof_kind="tx_hash", gross=it["amount"],
                currency=it["currency"], from_party=it["from"] or None)
            if res.get("ok"):
                added += 1

    parts = [f"маршрутов проверено {len(verified)}",
             f"входящих найдено {seen}",
             f"новых записано {added}"]
    if failed:
        parts.append("НЕ ОТВЕТИЛИ: " + "; ".join(failed[:3]))
    return "; ".join(parts)


if __name__ == "__main__":
    payment.seed()
    print("=" * 74)
    print("ПРОВЕРКА МАРШРУТОВ")
    print("=" * 74)
    for r in verify_routes():
        print(f"  {r['итог']:<12} {r['сеть']:<10} {r.get('адрес') or r.get('почему')}")
    print()
    print("НАБЛЮДЕНИЕ:", watch())
    print("СОСТОЯНИЯ ДЕНЕГ:", json.dumps(payment.state_of_money(), ensure_ascii=False))
