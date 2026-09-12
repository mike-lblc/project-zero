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

# ЧЕСТНЫЙ ЗАГОЛОВОК. Раньше здесь стоял заголовок браузера Chrome. Проверено
# 2026-09-13: все пять обозревателей отвечают 200 и честному роботу, так что
# маскировка не была нужна — а маскироваться под человека мы не должны вовсе.
UA = {"User-Agent": "P0-payment-watch/1.0 (read-only)", "Accept": "application/json"}

# ПРИЗНАННЫЕ ТОКЕНЫ — по адресу контракта, а НЕ по символу.
#
# Прежняя версия записывала доходом любой ERC-20 перевод на наш адрес и брала
# название валюты из символа токена. Любой может выпустить токен с символом
# «USDC» и разослать его на публичные адреса — такие рассылки обычное дело.
# Система записала бы его как настоящий USDC и объявила: «ПЛАТЁЖ! миссия
# доказана». Главное доказательство миссии подделывалось бы бесплатно.
#
# Адреса проверены живым запросом к обозревателю каждой сети 2026-09-13:
# символ USDC/USDC.E, миллионы держателей.
OFFICIAL_TOKENS = {
    "base": {"0x833589fcd6edb6e08f4c7c32d4f71b54bda02913": ("USDC", 6)},
    "ethereum": {"0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": ("USDC", 6)},
    "polygon": {"0x3c499c542cef5e3811e1192ce70d8cc03d5c3359": ("USDC", 6),
                "0x2791bca1f2de4661ed88a30c99a7a9449aa84174": ("USDC.e", 6)},
    "arbitrum": {"0xaf88d065e77c8cc2239327c5edb3a432268e5831": ("USDC", 6),
                 "0xff970a61a04b1ca14834a43f5de4533ebddb5cc8": ("USDC.e", 6)},
}
NATIVE = {"base": "ETH", "ethereum": "ETH", "arbitrum": "ETH", "polygon": "POL"}

NATIVE_TXS = {
    "base": "https://base.blockscout.com/api/v2/addresses/{addr}/transactions?filter=to",
    "ethereum": "https://eth.blockscout.com/api/v2/addresses/{addr}/transactions?filter=to",
    "polygon": "https://polygon.blockscout.com/api/v2/addresses/{addr}/transactions?filter=to",
    "arbitrum": "https://arbitrum.blockscout.com/api/v2/addresses/{addr}/transactions?filter=to",
}

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


def parse_token_transfers(items, net, addr):
    """Входящие переводы ПРИЗНАННЫХ токенов. Возвращает (найдено, отброшено).

    Отброшенное не молчит: число непризнанных токенов и нулевых переводов
    уходит в отчёт. Нулевой перевод — приём «отравления адреса», а не платёж.
    """
    known = OFFICIAL_TOKENS.get(net, {})
    found, ignored = [], {"непризнанный токен": 0, "нулевая сумма": 0}
    for t in items or []:
        to = ((t.get("to") or {}).get("hash") or "").lower()
        if to != addr.lower():
            continue          # исходящий перевод доходом не является
        h = t.get("transaction_hash") or t.get("tx_hash") or ""
        if not h:
            continue
        tok = t.get("token") or {}
        contract = (tok.get("address_hash") or tok.get("address") or "").lower()
        if contract not in known:
            ignored["непризнанный токен"] += 1
            continue
        symbol, dec = known[contract]
        raw = (t.get("total") or {}).get("value") or t.get("value") or "0"
        try:
            amount = int(raw) / (10 ** dec)
        except (TypeError, ValueError):
            continue
        if amount <= 0:
            ignored["нулевая сумма"] += 1
            continue
        found.append({"proof": h, "amount": amount, "currency": symbol, "network": net,
                      "from": ((t.get("from") or {}).get("hash") or "")})
    return found, ignored


def parse_native_txs(items, net, addr):
    """Входящие переводы родной монеты сети. Только успешные и ненулевые."""
    found = []
    for t in items or []:
        if ((t.get("to") or {}).get("hash") or "").lower() != addr.lower():
            continue
        if t.get("status") != "ok" or t.get("result") not in (None, "success"):
            continue          # упавшая транзакция денег не принесла
        try:
            amount = int(t.get("value") or 0) / 1e18
        except (TypeError, ValueError):
            continue
        if amount <= 0 or not t.get("hash"):
            continue
        found.append({"proof": t["hash"], "amount": amount, "currency": NATIVE[net],
                      "network": net, "from": ((t.get("from") or {}).get("hash") or "")})
    return found


def parse_btc_txs(txs, addr):
    """Входящие биткоин-переводы. Неподтверждённые — не деньги, а ожидание.

    Транзакция в мемпуле может не войти в блок никогда. Записать её полученной
    значит принять PAYMENT_PENDING за PAID — ровно то смешение состояний денег,
    которое директива запрещает.
    """
    found, pending = [], 0
    for tx in txs or []:
        h = tx.get("txid") or ""
        if not h:
            continue
        sats = sum(o.get("value", 0) for o in (tx.get("vout") or [])
                   if o.get("scriptpubkey_address") == addr)
        if sats <= 0:
            continue
        if not (tx.get("status") or {}).get("confirmed"):
            pending += 1
            continue
        found.append({"proof": h, "amount": sats / 1e8, "currency": "BTC",
                      "network": "bitcoin", "from": ""})
    return found, pending


def _incoming_evm(net, addr):
    """Входящие по сети EVM: признанные токены и родная монета."""
    d = _get(TRANSFERS[net].format(addr=addr))
    if d.get("__error__"):
        return None, d["__error__"], {}
    found, ignored = parse_token_transfers(d.get("items"), net, addr)
    n = _get(NATIVE_TXS[net].format(addr=addr))
    if n.get("__error__"):
        return None, f"родная монета: {n['__error__']}", ignored
    return found + parse_native_txs(n.get("items"), net, addr), None, ignored


def _incoming_btc(addr):
    d = _get(TRANSFERS["bitcoin"].format(addr=addr))
    if isinstance(d, dict) and d.get("__error__"):
        return None, d["__error__"], 0
    found, pending = parse_btc_txs(d if isinstance(d, list) else [], addr)
    return found, None, pending


def watch():
    """Обходит все ПРОВЕРЕННЫЕ маршруты и записывает новые поступления.

    Отказ обозревателя возвращается как отказ, а не как «денег нет»: это
    разные вещи, и мы уже теряли обход рынка, доложив молчание как пустоту.
    """
    verified = [r for r in payment.routes() if r["status"] == "verified"]
    if not verified:
        return "проверенных маршрутов нет — сперва проверка маршрутов"

    seen, failed, added, pending = 0, [], 0, 0
    ignored = {}
    route_of = {(r["currency"], r["network"]): r.get("id") for r in payment.routes()}
    # Одна сеть — один опрос: маршрутов в сети может быть несколько (USDC и
    # родная монета), а поступления в ней общие.
    for net in sorted({r.get("network") for r in verified if r.get("network") in TRANSFERS}):
        addr = (payment.OWNER_DESTINATIONS["btc"] if net == "bitcoin"
                else payment.OWNER_DESTINATIONS["evm"])
        if net == "bitcoin":
            items, err, pend = _incoming_btc(addr)
            pending += pend or 0
        else:
            items, err, ign = _incoming_evm(net, addr)
            for k, v in (ign or {}).items():
                ignored[k] = ignored.get(k, 0) + v
        if err:
            failed.append(f"{net}: {err}")
            continue
        seen += len(items)
        for it in items:
            res = payment.record_receipt(
                proof=it["proof"], proof_kind="tx_hash", gross=it["amount"],
                currency=it["currency"], from_party=it["from"] or None,
                network=net, route_id=route_of.get((it["currency"], net)))
            if res.get("ok"):
                added += 1

    parts = [f"маршрутов проверено {len(verified)}",
             f"входящих найдено {seen}",
             f"новых записано {added}"]
    if pending:
        parts.append(f"неподтверждённых биткоин-переводов {pending} — это ожидание, не деньги")
    if any(ignored.values()):
        parts.append("ОТБРОШЕНО: " + ", ".join(f"{k} {v}" for k, v in ignored.items() if v))
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
