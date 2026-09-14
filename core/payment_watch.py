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

# СЕТИ БЕЗ EVM-ОБОЗРЕВАТЕЛЯ. Solana — публичный RPC без ключа; Stacks — Hiro API без
# ключа. До 15.09 маршрут Solana стоял unverified «наблюдателя нет» — и деньги
# туда могли прийти незамеченными.
SOLANA_RPC = "https://api.mainnet-beta.solana.com"
SOLANA_USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
HIRO = "https://api.hiro.so/extended/v1"
SBTC_SUFFIX = ".sbtc-token::sbtc-token"
WATCHED_EXTRA = ("solana", "stacks")

# Срок зачисления и правило, после которого поступление засчитывается.
SETTLEMENT = {
    "base": "блок ~2 с; засчитывается после включения в блок",
    "ethereum": "блок ~12 с; засчитывается после включения в блок",
    "polygon": "блок ~2 с; засчитывается после включения в блок",
    "arbitrum": "блок <1 с; засчитывается после включения в блок",
    "bitcoin": "блок ~10 мин; засчитывается после первого подтверждения",
}

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
                     minimum_payout=0.0, failure_reason=None,
                     estimated_fee="комиссию сети платит отправитель; приём бесплатный",
                     country_eligibility="без ограничений: самостоятельное хранение, без посредника",
                     settlement_time=SETTLEMENT[net])
        # контракт — по валюте, из того же списка, по которому признаётся поступление
        for contract, (symbol, _) in OFFICIAL_TOKENS.get(net, {}).items():
            payment.mark("self-custody", network=net, currency=symbol, contract=contract)
        out.append({"сеть": net, "итог": "проверен",
                    "адрес": addr[:10] + "…" + addr[-6:]})
    # Solana и Stacks: проверка живым запросом к RPC/Hiro по нашему адресу.
    sol = payment.OWNER_DESTINATIONS["sol"]
    r = _rpc("getSignaturesForAddress", [sol, {"limit": 1}])
    if isinstance(r, dict) and r.get("__error__"):
        payment.mark("self-custody", network="solana", status="unverified",
                     failure_reason=f"RPC не ответил: {r['__error__']}")
        out.append({"сеть": "solana", "итог": "не проверен", "почему": r["__error__"]})
    else:
        payment.mark("self-custody", network="solana", status="verified", account_ready=1,
                     kyc_required=0, withdrawal_available=1, minimum_payout=0.0, failure_reason=None,
                     estimated_fee="комиссию сети платит отправитель; приём бесплатный",
                     country_eligibility="без ограничений: самостоятельное хранение",
                     settlement_time="финальность ~13 с")
        payment.mark("self-custody", network="solana", currency="USDC", contract=SOLANA_USDC)
        out.append({"сеть": "solana", "итог": "проверен", "адрес": sol[:8] + "…" + sol[-6:]})
    stx = payment.OWNER_DESTINATIONS.get("stx")
    if stx:
        d = _get(f"{HIRO}/address/{stx}/balances")
        if d.get("__error__"):
            payment.mark("self-custody", network="stacks", status="unverified",
                         failure_reason=f"Hiro не ответил: {d['__error__']}")
            out.append({"сеть": "stacks", "итог": "не проверен", "почему": d["__error__"]})
        else:
            payment.mark("self-custody", network="stacks", status="verified", account_ready=1,
                         kyc_required=0, withdrawal_available=1, minimum_payout=0.0, failure_reason=None,
                         estimated_fee="комиссию сети платит отправитель; приём бесплатный",
                         country_eligibility="кошелёк агента; seed у владельца",
                         settlement_time="блок ~5 мин; финальность по биткоину")
            payment.mark("self-custody", network="stacks", currency="sBTC",
                         contract="SM3VDXK3WZZSA84XXFKAFAF15NNZX32CTSG82JFQ4.sbtc-token")
            out.append({"сеть": "stacks", "итог": "проверен", "адрес": stx[:8] + "…" + stx[-6:]})
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


def _address_of(net):
    key = {"bitcoin": "btc", "solana": "sol", "stacks": "stx"}.get(net, "evm")
    return payment.OWNER_DESTINATIONS[key]


def _rpc(method, params, timeout=25):
    """JSON-RPC к Solana; ошибка возвращается словарём, а не тишиной."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(SOLANA_RPC, data=body, headers={**UA, "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8", "ignore"))
    except Exception as e:
        return {"__error__": f"{type(e).__name__}"}
    if isinstance(d, dict) and d.get("error"):
        return {"__error__": str(d["error"])[:120]}
    return d.get("result") if isinstance(d, dict) else {"__error__": "bad json"}


def parse_solana_tx(tx, sig, addr):
    """Входящие по одной подтверждённой транзакции Solana: SOL и USDC (официальный mint).

    Сумма — разница балансов нашего счёта до и после; отправитель — плательщик
    комиссии (первый ключ). Сбойная транзакция (meta.err) денег не принесла.
    """
    out = []
    if not isinstance(tx, dict) or (tx.get("meta") or {}).get("err") is not None:
        return out
    meta = tx.get("meta") or {}
    msg = (tx.get("transaction") or {}).get("message") or {}
    keys = [k.get("pubkey") if isinstance(k, dict) else k for k in (msg.get("accountKeys") or [])]
    sender = keys[0] if keys else ""
    if addr in keys:
        i = keys.index(addr)
        pre, post = meta.get("preBalances") or [], meta.get("postBalances") or []
        if i < len(pre) and i < len(post) and post[i] > pre[i] and sender != addr:
            out.append({"proof": sig, "amount": (post[i] - pre[i]) / 1e9, "currency": "SOL",
                        "network": "solana", "from": sender})
    def _tok(bal):
        return {(b.get("owner"), b.get("mint")): float((b.get("uiTokenAmount") or {}).get("uiAmount") or 0)
                for b in bal or [] if isinstance(b, dict)}
    pre_t, post_t = _tok(meta.get("preTokenBalances")), _tok(meta.get("postTokenBalances"))
    k = (addr, SOLANA_USDC)
    delta = post_t.get(k, 0.0) - pre_t.get(k, 0.0)
    if delta > 0 and sender != addr:
        out.append({"proof": sig, "amount": round(delta, 6), "currency": "USDC",
                    "network": "solana", "from": sender})
    return out


def _incoming_solana(addr, max_tx=8):
    sigs = _rpc("getSignaturesForAddress", [addr, {"limit": 25}])
    if isinstance(sigs, dict) and sigs.get("__error__"):
        return None, sigs["__error__"]
    found = []
    c = payment._con()
    known = {r[0] for r in c.execute("SELECT proof FROM payment_receipts")}
    c.close()
    for s in (sigs or [])[:25]:
        sig = s.get("signature")
        if not sig or sig in known or s.get("err") is not None:
            continue
        if s.get("confirmationStatus") not in (None, "finalized"):
            continue                      # подтверждённое, но не финальное — ожидание
        if max_tx <= 0:
            break
        max_tx -= 1
        tx = _rpc("getTransaction", [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])
        if isinstance(tx, dict) and tx.get("__error__"):
            return None, tx["__error__"]
        found.extend(parse_solana_tx(tx, sig, addr))
    return found, None


def parse_stacks_transfers(results, addr):
    """Входящие по Stacks: STX и sBTC (по идентификатору актива), только успешные."""
    found = []
    for r in results or []:
        tx = r.get("tx") or {}
        if tx.get("tx_status") != "success" or not tx.get("tx_id"):
            continue
        sender = tx.get("sender_address") or ""
        if sender == addr:
            continue
        for t in r.get("stx_transfers") or []:
            if t.get("recipient") == addr and int(t.get("amount") or 0) > 0:
                found.append({"proof": tx["tx_id"], "amount": int(t["amount"]) / 1e6, "currency": "STX",
                              "network": "stacks", "from": t.get("sender") or sender})
        for t in r.get("ft_transfers") or []:
            if (t.get("recipient") == addr and str(t.get("asset_identifier", "")).endswith(SBTC_SUFFIX)
                    and int(t.get("amount") or 0) > 0):
                found.append({"proof": tx["tx_id"], "amount": int(t["amount"]) / 1e8, "currency": "sBTC",
                              "network": "stacks", "from": t.get("sender") or sender})
    return found


def _incoming_stacks(addr):
    d = _get(f"{HIRO}/address/{addr}/transactions_with_transfers?limit=50")
    if isinstance(d, dict) and d.get("__error__"):
        return None, d["__error__"]
    return parse_stacks_transfers((d or {}).get("results"), addr), None


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
    watched = set(TRANSFERS) | set(WATCHED_EXTRA)
    for net in sorted({r.get("network") for r in verified if r.get("network") in watched}):
        addr = _address_of(net)
        if net == "bitcoin":
            items, err, pend = _incoming_btc(addr)
            pending += pend or 0
        elif net == "solana":
            items, err = _incoming_solana(addr)
        elif net == "stacks":
            items, err = _incoming_stacks(addr)
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
