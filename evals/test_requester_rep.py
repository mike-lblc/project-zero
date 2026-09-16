"""ОТБОР ЗАКАЗЧИКА НА TASKMARKET (16.09.2026).

За $2 соперничает около сотни подач, и единственное, чем мы распоряжаемся, — на чью
задачу потратить ход мастерового. Площадка публикует репутацию заказчика по адресу;
проверено на живых числах: 0x4363… закрыл 30 задач из 33 и ни разу не дал задаче
истечь без решения — на такого работать стоит. Заказчик с нулём закрытых при десятке
созданных собирает работы и молчит — на такого ход не тратится.

Новый заказчик НЕ отвергается: у каждого доказанного плательщика когда-то было ноль.
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class Verdicts(unittest.TestCase):
    def setUp(self):
        from agents import bounty
        self.b = bounty

    def rep(self, **kw):
        base = {"created": 0, "completed": 0, "expired_no_action": 0, "self_award": 0,
                "cancelled": 0, "submissions": 0, "workers": 0}
        base.update(kw)
        return base

    def test_proven_payer_is_taken(self):
        """Живые числа заказчика 0x4363…: 30 из 33, ноль истёкших."""
        take, odds, words = self.b.rep_verdict(self.rep(created=33, completed=30, submissions=2591, workers=430))
        self.assertTrue(take, words)
        self.assertIn("30 из 33", words)
        self.assertGreater(odds, 0)

    def test_collector_who_never_pays_is_skipped(self):
        take, _, words = self.b.rep_verdict(self.rep(created=12, completed=0, submissions=400))
        self.assertFalse(take)
        self.assertIn("НЕ ПЛАТИТ", words)

    def test_mostly_dead_tasks_are_skipped(self):
        take, _, words = self.b.rep_verdict(self.rep(created=20, completed=2, expired_no_action=16, submissions=300))
        self.assertFalse(take, words)

    def test_self_dealer_is_skipped(self):
        take, _, words = self.b.rep_verdict(self.rep(created=10, completed=9, self_award=8, submissions=90))
        self.assertFalse(take, words)
        self.assertIn("награждает себя", words)

    def test_new_requester_is_not_punished_for_being_new(self):
        take, _, words = self.b.rep_verdict(self.rep(created=1, completed=0, submissions=3))
        self.assertTrue(take, words)
        self.assertIn("новый", words)

    def test_unknown_reputation_does_not_block(self):
        take, odds, words = self.b.rep_verdict(None)
        self.assertTrue(take)
        self.assertIsNone(odds)

    def test_fewer_rivals_ranks_ahead_at_equal_reputation(self):
        """Порядок обхода: сначала сильный плательщик, при равенстве — где меньше соперников."""
        tasks = [{"id": "a", "_rep_odds": 0.01, "submissionCount": 100, "reward": "2000000"},
                 {"id": "b", "_rep_odds": 0.20, "submissionCount": 5, "reward": "1000000"},
                 {"id": "c", "_rep_odds": 0.20, "submissionCount": 2, "reward": "1000000"}]
        tasks.sort(key=lambda x: (-(x.get("_rep_odds") or 0), x.get("submissionCount") or 0,
                                  -float(x.get("reward") or 0)))
        self.assertEqual([t["id"] for t in tasks], ["c", "b", "a"])


class Caching(unittest.TestCase):
    """Репутация читается из таблицы, а не спрашивается у CLI на каждую задачу каждого хода."""

    def setUp(self):
        from core import db, execution, guard
        self.db, self.ex, self.guard = db, execution, guard
        self.saved = (db.DB_PATH, set(db._SCHEMA_DONE), db._WAL_SET, execution._MIGRATED[0], guard.check_action)
        self.tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(self.tmp.name) / "b.db"
        db._SCHEMA_DONE.clear(); db._WAL_SET = False; execution._MIGRATED[0] = False
        guard.check_action = lambda *a, **k: None
        db.init().close(); execution._con().close()

    def tearDown(self):
        (self.db.DB_PATH, done, self.db._WAL_SET, self.ex._MIGRATED[0], self.guard.check_action) = self.saved
        self.db._SCHEMA_DONE.clear(); self.db._SCHEMA_DONE.update(done)
        import gc; gc.collect()
        self.tmp.cleanup()

    def test_cli_asked_once_then_served_from_table(self):
        from agents import bounty
        calls = []

        def fake_tm(args, timeout=120):
            calls.append(list(args))
            return {"ok": True, "data": {"totalTasksCreated": 33, "completedCount": 30,
                                         "expiredNoActionCount": 0, "selfAwardCount": 0,
                                         "cancelledAfterSubmissionsCount": 0,
                                         "totalSubmissionAttempts": 2591, "totalUniqueWorkers": 430}}
        saved = bounty._tm
        bounty._tm = fake_tm
        try:
            c = self.db.connect(); bounty._tm_table(c)
            a = "0x436326b6772851Ca8Bd84F27e48d77A8668b34Bd"
            first = bounty._requester_rep(c, a, {})        # пустой кэш хода → спрашиваем CLI
            second = bounty._requester_rep(c, a, {})       # свежая запись в таблице → не спрашиваем
            c.commit(); c.close()
        finally:
            bounty._tm = saved
        self.assertEqual(first["completed"], 30)
        self.assertEqual(second["completed"], 30)
        self.assertEqual(len(calls), 1, f"CLI спрошен {len(calls)} раз(а), ожидался один: {calls}")

    def test_blank_address_never_reaches_cli(self):
        from agents import bounty
        saved = bounty._tm
        bounty._tm = lambda *a, **k: (_ for _ in ()).throw(AssertionError("CLI не должен вызываться"))
        try:
            c = self.db.connect(); bounty._tm_table(c)
            self.assertIsNone(bounty._requester_rep(c, "", {}))
            self.assertIsNone(bounty._requester_rep(c, None, {}))
            c.close()
        finally:
            bounty._tm = saved


if __name__ == "__main__":
    unittest.main()
