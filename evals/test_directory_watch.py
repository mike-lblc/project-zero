"""НАБЛЮДЕНИЕ ЗА КАТАЛОГОМ, КОТОРЫЙ ПЛАТИТ САМ (16.09.2026).

nohumans.directory — единственная найденная площадка, где покупатель платит нам, ничего
у нас не спрашивая. Платная проверка там — событие точечное: если она прошла и сорвалась,
каталог запомнит исход, а мы не узнаем. Поэтому переход в «оплачено» должен быть замечен
ОДИН раз и записан вместе с хешем расчёта — это и есть доказательство первого стороннего
платежа. Сеть в тестах не трогается: ответ каталога подменяется.
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class TempDB(unittest.TestCase):
    def setUp(self):
        from core import db, execution, guard
        self.db, self.ex, self.guard = db, execution, guard
        self.saved = (db.DB_PATH, set(db._SCHEMA_DONE), db._WAL_SET, execution._MIGRATED[0], guard.check_action)
        self.tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(self.tmp.name) / "d.db"
        db._SCHEMA_DONE.clear(); db._WAL_SET = False; execution._MIGRATED[0] = False
        guard.check_action = lambda *a, **k: None
        db.init().close(); execution._con().close()
        from agents import directory_watch as dw
        self.dw = dw
        self.saved_ids, self.saved_get = dw.listing_ids, dw._get
        dw.listing_ids = lambda: [("search", "aaa"), ("report", "bbb")]
        self.said = []
        from core import bus
        self.saved_bc = bus.broadcast
        bus.broadcast = lambda who, text: self.said.append(text)

    def tearDown(self):
        from core import bus
        self.dw.listing_ids, self.dw._get = self.saved_ids, self.saved_get
        bus.broadcast = self.saved_bc
        (self.db.DB_PATH, done, self.db._WAL_SET, self.ex._MIGRATED[0], self.guard.check_action) = self.saved
        self.db._SCHEMA_DONE.clear(); self.db._SCHEMA_DONE.update(done)
        import gc; gc.collect()
        self.tmp.cleanup()

    def reply(self, status="verified", paid=False, tx=None, fails=0):
        return {"status": status, "consecutive_failures": fails, "probe_count": 4,
                "paid_verification": {"verified": paid, **({"tx_hash": tx} if tx else {})}}


class Payment(TempDB):
    def test_payment_is_announced_once_with_its_hash(self):
        self.dw._get = lambda lid, timeout=30: self.reply(paid=(lid == "aaa"), tx="0xdead" if lid == "aaa" else None)
        first = self.dw.check()
        self.assertIn("ОПЛАЧЕН", first)
        self.assertTrue(any("0xdead" in s for s in self.said), self.said)
        self.said.clear()
        second = self.dw.check()                       # тот же ответ каталога
        self.assertIn("ОПЛАЧЕН", second)
        self.assertEqual(self.said, [], "о том же платеже объявлено повторно")

    def test_hash_is_kept_even_if_the_catalogue_stops_returning_it(self):
        self.dw._get = lambda lid, timeout=30: self.reply(paid=(lid == "aaa"), tx="0xdead" if lid == "aaa" else None)
        self.dw.check()
        self.dw._get = lambda lid, timeout=30: self.reply(paid=(lid == "aaa"))   # хеша больше нет
        self.dw.check()
        c = self.db.connect()
        tx = c.execute("SELECT paid_tx FROM directory_listing WHERE id='aaa'").fetchone()[0]
        c.close()
        self.assertEqual(tx, "0xdead")


class Liveness(TempDB):
    def test_falling_out_of_verified_is_reported(self):
        self.dw._get = lambda lid, timeout=30: self.reply()
        self.dw.check()
        self.said.clear()
        self.dw._get = lambda lid, timeout=30: (self.reply(status="failing", fails=3) if lid == "aaa" else self.reply())
        out = self.dw.check()
        self.assertIn("failing", out)
        self.assertTrue(any("сорвалось" in s for s in self.said), self.said)

    def test_a_listing_that_does_not_answer_does_not_look_like_a_drop(self):
        """Молчащий каталог — не то же самое, что снятое объявление: не выдумываем событие."""
        self.dw._get = lambda lid, timeout=30: self.reply()
        self.dw.check()
        self.said.clear()
        self.dw._get = lambda lid, timeout=30: None
        out = self.dw.check()
        self.assertIn("недоступно 2", out)
        self.assertEqual(self.said, [])

    def test_no_listings_configured_is_stated_plainly(self):
        self.dw.listing_ids = lambda: []
        self.assertIn("нечего", self.dw.check())


class Config(unittest.TestCase):
    def test_edit_tokens_are_never_read(self):
        """Наблюдение только читает: ключи правки объявлений ему не нужны."""
        src = (ROOT / "agents" / "directory_watch.py").read_text(encoding="utf-8")
        self.assertNotIn("NOHUMANS_TOKEN", src)
        self.assertNotIn("x-claim-token", src)

    def test_real_env_lists_every_paid_route(self):
        from agents import directory_watch
        from core.identity import TARIFF
        routes = {r for r, _ in directory_watch.listing_ids()}
        missing = {p.lstrip("/") for p in TARIFF} - routes
        self.assertEqual(missing, set(), f"платные маршруты без объявления в каталоге: {missing}")


if __name__ == "__main__":
    unittest.main()
