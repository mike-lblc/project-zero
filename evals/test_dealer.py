"""ДИЛЕР — сеть путей к первому платежу (MTBX.txt + решения владельца 15.09.2026).

Что закреплено.
- Каждое обращение несёт шесть элементов протокола (раздел VIII).
- Приоритет предпочитает того, у кого есть деньги и кто отвечает (раздел V).
- Платёж от самого владельца или с кошелька агента успехом не является (раздел II).
- Поступление засчитывается только по хешу из приёмника платежей; заявление — нет.
- Наблюдатели Solana и Stacks разбирают транзакции по фактам сети.
- Адрес кошелька в тексте Moltbook — только там, где он живёт по замеру.

База временная, сеть не трогается.
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TX = "0x" + "cd" * 32
THIRD_PARTY = "0x1111111111111111111111111111111111111111"


class TempDB(unittest.TestCase):
    def setUp(self):
        from core import db, execution, guard
        self.db, self.ex, self.guard = db, execution, guard
        self.saved = (db.DB_PATH, set(db._SCHEMA_DONE), db._WAL_SET,
                      execution._MIGRATED[0], guard.check_action)
        self.tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(self.tmp.name) / "dealer.db"
        db._SCHEMA_DONE.clear()
        db._WAL_SET = False
        execution._MIGRATED[0] = False
        guard.check_action = lambda *a, **k: None
        db.init().close()
        execution._con().close()

    def tearDown(self):
        (self.db.DB_PATH, done, self.db._WAL_SET,
         self.ex._MIGRATED[0], self.guard.check_action) = self.saved
        self.db._SCHEMA_DONE.clear()
        self.db._SCHEMA_DONE.update(done)
        import gc
        gc.collect()          # Windows: дескриптор базы освобождается до удаления папки
        self.tmp.cleanup()


class Compose(unittest.TestCase):
    def test_message_has_six_elements(self):
        from agents import dealer
        cp = {"handle": "buyerbot", "platform": "moltbook", "kind": "agent", "source": "moltbook_demand",
              "ref": "p1", "needs": "need x402 market ranking for my sellers"}
        text, ask = dealer.compose(cp, {"key": "moltbook:usdc", "allows_wallet_address": 1}, "abc123")
        self.assertIn("What you get", text)                       # 2 ценность
        self.assertIn("What we ask", text)                        # 3 просьба
        self.assertIn("0xECa891e34b3E5873181Fb779672564E198C55354", text)  # 4 адрес разрешён
        # владелец 15.09: платёж — в любом активе, на который есть адрес, не только USDC
        for alt in ("BTC", "SOL", "TRX", "USDT"):
            self.assertIn(alt, text, alt)
        self.assertIn("any asset we accept", text)
        self.assertIn(f"{dealer.RESPONSE_WINDOW_H} hours", text)  # 5 срок
        self.assertIn("/health", text)                            # 6 проверка нашей части
        self.assertIn("abc123", text)
        self.assertTrue(text.startswith("You "))                  # 1 почему именно он
        self.assertEqual(ask["currency"], "USDC")

    def test_address_hidden_where_not_allowed(self):
        from agents import dealer
        cp = {"handle": "x", "platform": "moltbook", "kind": "agent", "source": "moltbook_inbound", "needs": ""}
        text, _ = dealer.compose(cp, {"key": "moltbook:general", "allows_wallet_address": 0})
        self.assertNotIn("0xECa891e34b3E5873181Fb779672564E198C55354", text)
        self.assertIn("402 response carries the receiving address", text)


class Priority(unittest.TestCase):
    def test_money_and_replies_rank_higher(self):
        from agents import dealer
        rich_replying = dealer._priority(0.9, 0.6, 0.5, 0.5, True, 1, 1, 48)
        poor_silent = dealer._priority(0.2, 0.1, 0.5, 0.5, False, 1, 0, 240)
        untouched = dealer._priority(0.5, 0.2, 0.5, 0.5, False, 0, 0, 96)
        self.assertGreater(rich_replying, untouched)
        self.assertGreater(untouched, poor_silent, "нетронутый контрагент лучше молчащего после письма")


class Observers(unittest.TestCase):
    def test_solana_native_and_usdc_parsed(self):
        from core import payment_watch as w
        addr = "FTbVqWwsfJJ5AuAwNDuCuzdpwCEJahu14HAUgAYcJHuq"
        tx = {"meta": {"err": None, "preBalances": [5_000_000_000, 100], "postBalances": [4_990_000_000, 1_000_000_100],
                       "preTokenBalances": [{"owner": addr, "mint": w.SOLANA_USDC, "uiTokenAmount": {"uiAmount": 1.0}}],
                       "postTokenBalances": [{"owner": addr, "mint": w.SOLANA_USDC, "uiTokenAmount": {"uiAmount": 3.5}}]},
              "transaction": {"message": {"accountKeys": [{"pubkey": "Sender111"}, {"pubkey": addr}]}}}
        items = w.parse_solana_tx(tx, "sig1", addr)
        self.assertEqual({(i["currency"], round(i["amount"], 6)) for i in items}, {("SOL", 1.0), ("USDC", 2.5)})
        self.assertTrue(all(i["from"] == "Sender111" for i in items))
        self.assertEqual(w.parse_solana_tx({"meta": {"err": {"x": 1}}}, "sig2", addr), [])

    def test_stacks_sbtc_and_stx_parsed_only_when_successful(self):
        from core import payment_watch as w
        addr = "SP34GH04YTB01AMXF4CAQ10Y5B7G4E0119N99W986"
        results = [
            {"tx": {"tx_id": "0xaaa", "tx_status": "success", "sender_address": "SPSENDER"},
             "stx_transfers": [{"recipient": addr, "amount": "2500000", "sender": "SPSENDER"}],
             "ft_transfers": [{"recipient": addr, "amount": "150",
                               "asset_identifier": "SM3VDXK3WZZSA84XXFKAFAF15NNZX32CTSG82JFQ4.sbtc-token::sbtc-token"}]},
            {"tx": {"tx_id": "0xbbb", "tx_status": "abort_by_response", "sender_address": "SPX"},
             "stx_transfers": [{"recipient": addr, "amount": "999"}]},
            {"tx": {"tx_id": "0xccc", "tx_status": "success", "sender_address": addr},
             "stx_transfers": [{"recipient": addr, "amount": "999"}]},
        ]
        items = w.parse_stacks_transfers(results, addr)
        self.assertEqual({(i["currency"], i["amount"]) for i in items}, {("STX", 2.5), ("sBTC", 1.5e-6)})


class MoltbookAddressPolicy(unittest.TestCase):
    def test_gate_opens_only_with_flag(self):
        from core import moltbook
        text = ("Pay 0.01 USDC to 0xECa891e34b3E5873181Fb779672564E198C55354 on Base for one ranked query; "
                "verify first at /health.")
        with self.assertRaises(moltbook.UnsafeContent):
            moltbook._clean_content(text)
        self.assertEqual(moltbook._clean_content(text, allow_addresses=True), text)


class Network(TempDB):
    def _demand(self, author="paybot", post="p-1", title="Will pay 5 USDC for x402 market ranking"):
        c = self.db.connect()
        c.execute("CREATE TABLE IF NOT EXISTS moltbook_demand (post_id TEXT PRIMARY KEY, submolt TEXT, "
                  "author TEXT, title TEXT, body TEXT, created_at TEXT, found_at TEXT, escalated_at TEXT, "
                  "answered_at TEXT)")
        c.execute("INSERT OR IGNORE INTO moltbook_demand VALUES (?,?,?,?,?,?,?,?,?)",
                  (post, "usdc", author, title, "need a ranked read of x402 sellers, paying in USDC",
                   "2026-09-15T00:00:00Z", "2026-09-15T00:00:00+00:00", None, None))
        c.commit(); c.close()

    def test_weave_survey_model_state_and_dry_act(self):
        from agents import dealer
        self._demand()
        w = dealer.weave()
        self.assertGreaterEqual(w["channels"], len(dealer.KNOWN_CHANNELS) + 1)
        s = dealer.survey()
        self.assertGreaterEqual(s["counterparties"], 1)
        dealer.model()
        top = dealer.top_targets(3)
        self.assertEqual(top[0]["handle"], "paybot")
        self.assertGreater(top[0]["priority"], 0)
        a = dealer.act(dry_run=True)
        self.assertEqual(len(a["sent"]), 1)
        self.assertIn("(dry)", a["sent"][0])
        st = dealer.state()
        self.assertFalse(st["success"])
        self.assertEqual(st["counterparties"]["total"], s["counterparties"])
        self.assertIsNotNone(dealer.last_state())

    def test_owner_or_agent_wallet_payment_is_never_income(self):
        from core import payment
        payment.seed()
        for own in payment.OWNER_DESTINATIONS.values():
            with self.assertRaises(ValueError):
                payment.record_receipt(proof=TX, proof_kind="tx_hash", gross=1.0, currency="USDC",
                                       from_party=own, network="base")

    def test_third_party_hash_is_the_only_success(self):
        from agents import dealer
        from core import payment
        payment.seed()
        dealer.weave()
        v = dealer.verify()
        self.assertFalse(v["success"], "без поступления успеха нет")
        r = payment.record_receipt(proof=TX, proof_kind="tx_hash", gross=0.01, currency="USDC",
                                   from_party=THIRD_PARTY, network="base")
        self.assertTrue(r["ok"])
        v = dealer.verify()
        self.assertTrue(v["success"])
        self.assertEqual(v["receipts"], 1)
        c = self.db.connect()
        row = c.execute("SELECT sender_kind, from_party FROM dealer_receipts").fetchone()
        c.close()
        self.assertEqual(tuple(row), ("unknown", THIRD_PARTY))


if __name__ == "__main__":
    unittest.main()
