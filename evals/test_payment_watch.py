"""ПРИЁМНИК ДЕНЕГ — главное доказательство миссии не должно подделываться.

Что здесь закреплено.

1. Любой может выпустить токен с символом «USDC» и разослать его на публичные
   адреса. Прежний наблюдатель записывал такой перевод как настоящий USDC и
   объявлял «ПЛАТЁЖ! миссия доказана». Признание идёт по адресу контракта.
2. Нулевой перевод — приём «отравления адреса», упавшая транзакция денег не
   принесла, неподтверждённый биткоин — ожидание, а не поступление.
3. Поступления писались в payment_receipts, а четырнадцать читателей —
   дашборд, экономика, облачная проверка миссии — смотрели в payments, куда
   больше никто не писал. Настоящий платёж показался бы нулём.

Сеть не нужна: ответы обозревателей подставляются, база — временная.
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import payment_watch as w  # noqa: E402

OURS = "0xECa891e34b3E5873181Fb779672564E198C55354"
REAL_USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
FAKE_USDC = "0x1111111111111111111111111111111111111111"
TX = "0x" + "ab" * 32


def transfer(contract, value, to=OURS, symbol="USDC", h=TX):
    return {"to": {"hash": to}, "from": {"hash": "0x" + "22" * 20},
            "transaction_hash": h, "total": {"value": str(value)},
            "token": {"address_hash": contract, "symbol": symbol, "decimals": "6"}}


class Tokens(unittest.TestCase):
    def test_fake_usdc_by_symbol_is_rejected(self):
        found, ignored = w.parse_token_transfers([transfer(FAKE_USDC, 5_000_000)], "base", OURS)
        self.assertEqual(found, [], "токен с символом USDC и чужим контрактом принят за доход")
        self.assertEqual(ignored["непризнанный токен"], 1)

    def test_official_usdc_is_accepted(self):
        found, _ = w.parse_token_transfers([transfer(REAL_USDC_BASE, 5_000_000)], "base", OURS)
        self.assertEqual(len(found), 1)
        self.assertEqual((found[0]["currency"], found[0]["amount"], found[0]["network"]),
                         ("USDC", 5.0, "base"))

    def test_zero_transfer_is_not_income(self):
        found, ignored = w.parse_token_transfers([transfer(REAL_USDC_BASE, 0)], "base", OURS)
        self.assertEqual(found, [])
        self.assertEqual(ignored["нулевая сумма"], 1)

    def test_outgoing_is_not_income(self):
        found, _ = w.parse_token_transfers(
            [transfer(REAL_USDC_BASE, 5_000_000, to="0x" + "33" * 20)], "base", OURS)
        self.assertEqual(found, [])

    def test_usdc_on_other_network_contract_is_not_base_usdc(self):
        """Контракт из одной сети не признаётся в другой."""
        found, _ = w.parse_token_transfers([transfer(REAL_USDC_BASE, 5_000_000)], "polygon", OURS)
        self.assertEqual(found, [])


class Native(unittest.TestCase):
    def tx(self, value, status="ok", result="success"):
        return {"hash": TX, "value": str(value), "status": status, "result": result,
                "to": {"hash": OURS}, "from": {"hash": "0x" + "22" * 20}}

    def test_successful_native_transfer_counts(self):
        found = w.parse_native_txs([self.tx(10 ** 16)], "base", OURS)
        self.assertEqual((found[0]["currency"], found[0]["amount"]), ("ETH", 0.01))

    def test_failed_or_zero_native_transfer_does_not(self):
        self.assertEqual(w.parse_native_txs([self.tx(10 ** 16, "error", "Reverted")], "base", OURS), [])
        self.assertEqual(w.parse_native_txs([self.tx(0)], "base", OURS), [])


class Bitcoin(unittest.TestCase):
    ADDR = "bc1qqwgyyqv6raq2jnghals2n2aujgwd4e9p64g4hr"

    def tx(self, confirmed):
        return {"txid": "f" * 64, "status": {"confirmed": confirmed},
                "vout": [{"scriptpubkey_address": self.ADDR, "value": 25_000}]}

    def test_unconfirmed_is_pending_not_received(self):
        found, pending = w.parse_btc_txs([self.tx(False)], self.ADDR)
        self.assertEqual((found, pending), ([], 1))

    def test_confirmed_is_received(self):
        found, pending = w.parse_btc_txs([self.tx(True)], self.ADDR)
        self.assertEqual((len(found), pending, found[0]["amount"]), (1, 0, 0.00025))


class ReceiptMirror(unittest.TestCase):
    """Поступление видно и в новой таблице, и в прежней, которую читает дашборд."""

    def setUp(self):
        from core import db
        self.db = db
        self.saved = (db.DB_PATH, set(db._SCHEMA_DONE), db._WAL_SET)
        self.tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(self.tmp.name) / "test.db"
        db._SCHEMA_DONE.clear()
        db._WAL_SET = False
        db.init().close()

    def tearDown(self):
        self.db.DB_PATH, done, self.db._WAL_SET = self.saved
        self.db._SCHEMA_DONE.clear()
        self.db._SCHEMA_DONE.update(done)
        self.tmp.cleanup()

    def test_receipt_is_mirrored_with_network(self):
        from core import payment
        res = payment.record_receipt(TX, "tx_hash", 5.0, "USDC", network="base",
                                     from_party="0x" + "22" * 20)
        self.assertTrue(res["ok"])
        c = self.db.connect()
        rec = c.execute("SELECT network, net FROM payment_receipts").fetchall()
        legacy = c.execute("SELECT chain, amount, asset FROM payments").fetchall()
        c.close()
        self.assertEqual([tuple(r) for r in rec], [("base", 5.0)])
        self.assertEqual([tuple(r) for r in legacy], [("base", "5.0", "USDC")],
                         "поступление не видно в таблице, которую читает дашборд")

    def test_duplicate_and_owner_and_zero_are_refused(self):
        from core import payment
        payment.record_receipt(TX, "tx_hash", 5.0, "USDC", network="base")
        self.assertFalse(payment.record_receipt(TX, "tx_hash", 5.0, "USDC", network="base")["ok"])
        with self.assertRaises(ValueError):
            payment.record_receipt("0x" + "cd" * 32, "tx_hash", 5.0, "USDC",
                                   from_party=payment.OWNER_DESTINATIONS["evm"])
        with self.assertRaises(ValueError):
            payment.record_receipt("0x" + "ef" * 32, "tx_hash", 0, "USDC")




class Tron(unittest.TestCase):
    """Снимки Tronscan по адресу владельца сняты 15.09.2026 (evals/fixtures)."""
    FX = ROOT / "evals" / "fixtures"

    def _load(self, name):
        import json as _j
        return _j.loads((self.FX / name).read_text(encoding="utf-8"))

    def test_history_before_mission_is_not_income(self):
        # На адресе есть 3 500 USDT из июля и TRX-пыль — всё раньше начала миссии.
        addr = "TB9rHqT8yLxwdsWCb3zN2nvjc8wLhsUdaQ"
        self.assertEqual(w.parse_trc20(self._load("tron_trc20.json")["token_transfers"], addr), [])
        self.assertEqual(w.parse_tron_txs(self._load("tron_transactions.json")["data"], addr), [])

    def test_fresh_usdt_and_trx_count_but_spam_tokens_do_not(self):
        addr = "TB9rHqT8yLxwdsWCb3zN2nvjc8wLhsUdaQ"
        ts = 1789600000000     # после начала миссии, миллисекунды
        trc = [{"transaction_id": "a" * 64, "to_address": addr, "from_address": "TSender", "quant": "2500000",
                "contract_address": w.TRON_USDT, "confirmed": True, "contractRet": "SUCCESS", "block_ts": ts,
                "tokenInfo": {"tokenDecimal": 6}},
               {"transaction_id": "b" * 64, "to_address": addr, "from_address": "TSender", "quant": "99000000",
                "contract_address": "TFakeUSDT000000000000000000000000", "confirmed": True, "block_ts": ts,
                "tokenInfo": {"tokenDecimal": 6}}]
        got = w.parse_trc20(trc, addr)
        self.assertEqual([(g["currency"], g["amount"]) for g in got], [("USDT", 2.5)])
        txs = [{"hash": "c" * 64, "contractType": 1, "toAddress": addr, "ownerAddress": "TSender", "amount": 3000000,
                "confirmed": True, "contractRet": "SUCCESS", "timestamp": ts, "tokenInfo": {"tokenAbbr": "trx"}},
               {"hash": "d" * 64, "contractType": 2, "toAddress": addr, "ownerAddress": "TSpam", "amount": 18777989,
                "confirmed": True, "contractRet": "SUCCESS", "timestamp": ts, "tokenInfo": {"tokenAbbr": "Gas35COM"}},
               {"hash": "e" * 64, "contractType": 1, "toAddress": addr, "ownerAddress": "TSender", "amount": 3000000,
                "confirmed": False, "contractRet": "SUCCESS", "timestamp": ts, "tokenInfo": {"tokenAbbr": "trx"}}]
        got = w.parse_tron_txs(txs, addr)
        self.assertEqual([(g["currency"], g["amount"]) for g in got], [("TRX", 3.0)])


class Freshness(unittest.TestCase):
    def test_transfer_before_mission_start_is_dropped_on_evm(self):
        addr = "0x" + "ab" * 20
        old = {"transaction_hash": "0x" + "1" * 64, "timestamp": "2026-08-01T10:00:00.000000Z",
               "to": {"hash": addr}, "from": {"hash": "0x" + "cd" * 20},
               "token": {"address_hash": "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"}, "total": {"value": "5000000"}}
        new = dict(old, transaction_hash="0x" + "2" * 64, timestamp="2026-09-14T10:00:00.000000Z")
        found, _ = w.parse_token_transfers([old, new], "base", addr)
        self.assertEqual([f["proof"] for f in found], ["0x" + "2" * 64])

    def test_missing_timestamp_is_treated_as_fresh(self):
        self.assertTrue(w._fresh(None)); self.assertTrue(w._fresh(""))
        self.assertFalse(w._fresh("2026-01-01T00:00:00+00:00"))
        self.assertTrue(w._fresh(1789600000))            # секунды, после начала
        self.assertFalse(w._fresh(1782903069000))        # миллисекунды, июль


class AnyAsset(unittest.TestCase):
    def test_official_usdt_dai_weth_are_recognised_on_base(self):
        addr = "0x" + "ab" * 20
        items = [{"transaction_hash": "0x" + str(i) * 64, "to": {"hash": addr}, "from": {"hash": "0x" + "cd" * 20},
                  "token": {"address_hash": c}, "total": {"value": v}}
                 for i, (c, v) in enumerate([("0xfde4c96c8593536e31f229ea8f37b2ada2699bb2", "1000000"),
                                             ("0x50c5725949a6f0c72e6c4a641f24049a917db0cb", str(10 ** 18)),
                                             ("0x4200000000000000000000000000000000000006", str(2 * 10 ** 17))], 1)]
        found, ignored = w.parse_token_transfers(items, "base", addr)
        self.assertEqual(sorted((f["currency"], f["amount"]) for f in found), [("DAI", 1.0), ("USDT", 1.0), ("WETH", 0.2)])
        self.assertEqual(ignored["непризнанный токен"], 0)

    def test_accepted_list_covers_every_owner_address_and_is_not_usdc_only(self):
        from core import payment as p
        line = p.accepted_line()
        for k, addr in p.OWNER_DESTINATIONS.items():
            self.assertIn(addr, line, k)
        for asset in ("ETH", "BTC", "SOL", "TRX", "USDT", "STX"):
            self.assertIn(asset, line)
        self.assertTrue(any(a["address"] == p.OWNER_DESTINATIONS["tron"] for a in p.accepted()))

    def test_usd_value_stable_is_one_to_one_and_unknown_is_none(self):
        from core import payment as p
        self.assertEqual(p.usd_value("USDT", "2.5"), 2.5)
        self.assertEqual(p.usd_value("usdc", 3), 3.0)
        self.assertIsNone(p.usd_value("GAS35COM", 1))

if __name__ == "__main__":
    unittest.main()
