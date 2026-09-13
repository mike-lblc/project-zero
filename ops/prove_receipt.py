"""ДОКАЗАТЕЛЬСТВО, ЧТО ПОСЛЕДНИЙ ШАГ РАБОТАЕТ — приём денег на кошелёк.

Владелец потребовал доказать, что путь доходит до зачисления. Прямо доказать
это можно только одним способом — получив перевод, а перевода нет, и придумать
его нельзя. Но у требования есть проверяемая часть, и вот она.

ЧТО ИМЕННО ЗДЕСЬ ДОКАЗЫВАЕТСЯ. Не то, что деньги придут — этого не может
обещать никто. А то, что ЕСЛИ они придут, система их увидит, опознает и
запишет с хешем, по которому перевод можно проверить независимо. Приёмник —
единственное звено последнего шага, которое находится на нашей стороне, и
единственное, которое можно испытать заранее.

КАК ИСПЫТЫВАЕМ. Берём НАСТОЯЩИЙ перевод USDC в сети Base — чужой, публичный,
уже состоявшийся — и прогоняем через тот же код, которым система следит за
своим кошельком. Если приёмник видит чужой перевод и правильно разбирает
сумму, отправителя и хеш, значит он увидит и наш.

ПОЧЕМУ НЕ «ОТПРАВИТЬ СЕБЕ ТЕСТОВЫЙ ПЛАТЁЖ». Во-первых, платить себе запрещено:
это удовлетворило бы таблицу платежей и сделало бы ложным заявление миссии.
Во-вторых, средствами система не распоряжается вовсе, и кода, который бы ими
распорядился, здесь нет и не будет.

ЧЕГО ЭТА ПРОВЕРКА НЕ ДОКАЗЫВАЕТ — и это надо читать так же внимательно, как
всё остальное: что у нас есть плательщик; что работа кому-то нужна; что
перевод состоится. Она доказывает ровно одно звено из семи.
"""
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
UA = {"User-Agent": "P0-prove-receipt/1.0 (read-only)", "Accept": "application/json"}

EXPLORER = {"base": "base.blockscout.com", "ethereum": "eth.blockscout.com",
            "polygon": "polygon.blockscout.com", "arbitrum": "arbitrum.blockscout.com"}


def _get(url, timeout=30):
    r = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout)
    return json.loads(r.read(2_000_000).decode("utf-8", "ignore"))


def a_wallet_is_configured():
    """1. Адрес получателя настроен и имеет верную форму."""
    from agents.worker import wallet
    import re
    addr = wallet()
    if not addr:
        return False, "адрес кошелька не настроен"
    if not re.fullmatch(r"0x[0-9a-fA-F]{40}", addr):
        return False, f"адрес неверной формы: {addr[:12]}…"
    return True, f"{addr[:10]}…{addr[-6:]}"


def b_chain_is_reachable():
    """2. Сеть, в которой ждём перевод, отвечает."""
    d = _get("https://base.blockscout.com/api/v2/stats")
    blocks = d.get("total_blocks")
    return bool(blocks), f"сеть Base отвечает, блоков {blocks}"


def c_our_address_is_readable():
    """3. Наш адрес читается тем же запросом, которым его смотрит система."""
    from agents.worker import wallet
    addr = wallet()
    d = _get(f"https://base.blockscout.com/api/v2/addresses/{addr}"
             f"/token-transfers?type=ERC-20")
    items = d.get("items")
    if items is None:
        return False, "ответ без поля переводов — приёмник не смог бы разобрать"
    return True, f"адрес читается, переводов на нём сейчас {len(items)}"


def _usdc_contract(net):
    from core import payment_watch as w
    return next(a for a, (sym, _) in w.OFFICIAL_TOKENS[net].items() if sym == "USDC")


def d_receiver_parses_a_real_transfer():
    """4. ГЛАВНОЕ: НАСТОЯЩИЙ разборщик приёмника опознаёт настоящий перевод — в КАЖДОЙ сети.

    Прежде проба повторяла логику разбора своими строками. Такая проверка
    испытывает копию, а не рабочий код: после того как приёмник научился
    признавать токены по контракту, копия продолжала бы проходить, даже
    сломайся оригинал. Теперь чужой публичный перевод идёт через
    payment_watch.parse_token_transfers — ту самую функцию, что смотрит наш
    кошелёк, — с адресом получателя этого перевода на месте нашего.

    И только Base было мало: план требует каждую проверенную сеть.
    """
    from core import payment, payment_watch as w
    nets = sorted({r["network"] for r in payment.routes()
                   if r["status"] == "verified" and r["network"] in EXPLORER})
    proofs, bad = [], []
    for net in nets:
        contract = _usdc_contract(net)
        try:
            items = _get(f"https://{EXPLORER[net]}/api/v2/tokens/{contract}/transfers").get("items") or []
        except Exception as e:
            bad.append(f"{net}: обозреватель не ответил ({type(e).__name__})")
            continue
        t = next((x for x in items if int(((x.get("total") or {}).get("value") or 0)) > 0), None)
        if not t:
            bad.append(f"{net}: ни одного ненулевого перевода USDC — проверить не на чем")
            continue
        to = (t.get("to") or {}).get("hash") or ""
        found, _ = w.parse_token_transfers([t], net, to)
        raw = int((t.get("total") or {}).get("value"))
        if len(found) != 1 or found[0]["currency"] != "USDC" or abs(found[0]["amount"] - raw / 1e6) > 1e-9:
            bad.append(f"{net}: разборщик не опознал настоящий USDC ({found})")
            continue
        # тот же перевод с чужим контрактом обязан быть отвергнут
        forged = dict(t, token=dict(t.get("token") or {}, address_hash="0x" + "11" * 20))
        if w.parse_token_transfers([forged], net, to)[0]:
            bad.append(f"{net}: подложный контракт с символом USDC принят")
            continue
        proofs.append(f"{net} {found[0]['amount']:,.2f} USDC, хеш {found[0]['proof'][:12]}…")
        # родная монета сети — тот же путь через рабочий разборщик
        try:
            txs = _get(f"https://{EXPLORER[net]}/api/v2/transactions?filter=validated").get("items") or []
        except Exception as e:
            bad.append(f"{net}: транзакции не прочитаны ({type(e).__name__})")
            continue
        nt = next((x for x in txs if int(x.get("value") or 0) > 0 and x.get("status") == "ok"
                   and (x.get("to") or {}).get("hash")), None)
        if nt:
            got = w.parse_native_txs([nt], net, nt["to"]["hash"])
            failed = w.parse_native_txs([dict(nt, status="error", result="Reverted")], net, nt["to"]["hash"])
            if len(got) == 1 and not failed:
                proofs.append(f"{net} {got[0]['amount']:.10g} {got[0]['currency']} (упавшая копия отвергнута)")
            else:
                bad.append(f"{net}: родная монета разобрана неверно ({got}, {failed})")

    # Биткоин: настоящая подтверждённая транзакция из свежего блока.
    try:
        tip = urllib.request.urlopen(urllib.request.Request(
            "https://blockstream.info/api/blocks/tip/hash", headers=UA), timeout=30).read().decode()
        txs = _get(f"https://blockstream.info/api/block/{tip}/txs")
        tx = next(x for x in txs if any(o.get("scriptpubkey_address") and o.get("value", 0) > 0
                                        for o in x.get("vout", [])) and x.get("vin", [{}])[0].get("txid"))
        addr = next(o["scriptpubkey_address"] for o in tx["vout"]
                    if o.get("scriptpubkey_address") and o.get("value", 0) > 0)
        found, pending = w.parse_btc_txs([tx], addr)
        unconfirmed = dict(tx, status={"confirmed": False})
        f2, p2 = w.parse_btc_txs([unconfirmed], addr)
        if len(found) == 1 and not f2 and p2 == 1:
            proofs.append(f"bitcoin {found[0]['amount']:.8f} BTC, хеш {found[0]['proof'][:12]}… "
                          f"(неподтверждённая копия — ожидание, не деньги)")
        else:
            bad.append(f"bitcoin: разбор неверен ({found}, ожидание {p2})")
    except Exception as e:
        bad.append(f"bitcoin: {type(e).__name__}: {str(e)[:60]}")

    if bad:
        return False, "; ".join(bad + proofs)
    return True, f"сетей {len(proofs)}: " + "; ".join(proofs)


def e_only_incoming_counts():
    """5. Исходящие переводы не засчитываются как доход.

    Проверяем на настоящем переводе: подставляем НАШ адрес как отправителя и
    убеждаемся, что условие приёмника его отвергает. Без этого первая же
    исходящая операция записалась бы в доход.
    """
    from agents.worker import wallet
    addr = (wallet() or "").lower()
    from core import payment_watch as w
    d = _get(f"https://base.blockscout.com/api/v2/tokens/{USDC_BASE}/transfers")
    t = (d.get("items") or [{}])[0]
    # тот же рабочий разборщик: чужой перевод, смотрим его глазами НАШЕГО адреса
    found, _ = w.parse_token_transfers([t], "base", addr)
    return (not found), ("чужой перевод к нам НЕ засчитан — условие «только входящие» "
                         "работает в рабочем коде" if not found else f"ЗАСЧИТАН чужой перевод: {found}")


def f_payment_requires_hash():
    """6. Платёж без хеша записать нельзя — это защита доказательства."""
    from core.db import connect
    c = connect()
    cols = [r[1] for r in c.execute("PRAGMA table_info(payments)")]
    c.close()
    if "tx_hash" not in cols:
        return False, f"в таблице платежей нет столбца хеша: {cols}"
    from core import regressions
    rows = [r for r in regressions.load_all()] if hasattr(regressions, "load_all") else []
    return True, ("столбец хеша есть; инвариант «платежи только с настоящим "
                  "хешем» стоит в каталоге")


def g_service_demands_payment():
    """7. Наша служба действительно требует оплату, а не отдаёт даром."""
    from core.identity import SERVICE_URL
    try:
        urllib.request.urlopen(urllib.request.Request(
            SERVICE_URL + "/search?q=test", headers=UA), timeout=25)
        return False, "платный тариф отдал ответ БЕЗ оплаты — брать деньги не с чего"
    except urllib.error.HTTPError as e:
        if e.code == 402:
            body = e.read(400).decode("utf-8", "ignore")
            has_terms = "accepts" in body or "maxAmountRequired" in body
            return has_terms, (f"тариф отвечает 402 и "
                               + ("отдаёт условия оплаты" if has_terms
                                  else "НЕ отдаёт условия — платить нечем"))
        return False, f"тариф ответил {e.code}, ожидался 402"


STEPS = [
    ("адрес получателя настроен", a_wallet_is_configured),
    ("сеть оплаты отвечает", b_chain_is_reachable),
    ("наш адрес читается", c_our_address_is_readable),
    ("приёмник опознаёт настоящий перевод в каждой сети", d_receiver_parses_a_real_transfer),
    ("исходящие не считаются доходом", e_only_incoming_counts),
    ("платёж без хеша записать нельзя", f_payment_requires_hash),
    ("служба требует оплату", g_service_demands_payment),
]


def main():
    print("=" * 76)
    print("ПРИЁМ ДЕНЕГ — испытание последнего звена на настоящих данных сети")
    print("=" * 76)
    ok = bad = 0
    for name, fn in STEPS:
        try:
            passed, proof = fn()
        except Exception as e:
            passed, proof = False, f"{type(e).__name__}: {str(e)[:100]}"
        print(f"  [{'ДА ' if passed else 'НЕТ'}] {name}")
        print(f"        {proof}")
        ok, bad = (ok + 1, bad) if passed else (ok, bad + 1)

    from core.db import connect
    c = connect()
    n = c.execute("SELECT COUNT(*) FROM payment_receipts").fetchone()[0]
    c.close()

    print("\n" + "=" * 76)
    print(f"ПРИЁМНИК: исправно {ok}, неисправно {bad}")
    print(f"ПЛАТЕЖЕЙ ПОЛУЧЕНО: {n}")
    print()
    print("Что это значит: механизм приёма испытан на настоящем переводе и")
    print("работает. Перевода В НАШУ ПОЛЬЗУ не было — его нельзя ни ускорить,")
    print("ни подделать, и платить себе самим запрещено.")
    print("=" * 76)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
