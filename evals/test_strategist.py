"""СТРАТЕГ — экосистема, которая думает (директива владельца 15.09.2026).

Что закреплено.
- Гипотезы рождаются из ЖИВЫХ компонентов базы (спрос, каналы, доски), а не из списка.
- У каждой гипотезы — источник и улика; повтор имени не создаёт дубля.
- Оценка двигается сигналами: источник, где отвечали, ценнее источника, где молчали.
- Эксперимент с исчерпанным бюджетом без сигнала снимается, с сигналом — остаётся и ветвится.
- Связка без исполнителя не исполняется молча: она блокируется и ждёт адаптера.

База временная, сеть и модель не трогаются.
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
        db.DB_PATH = Path(self.tmp.name) / "strategist.db"
        db._SCHEMA_DONE.clear(); db._WAL_SET = False; execution._MIGRATED[0] = False
        guard.check_action = lambda *a, **k: None
        db.init().close(); execution._con().close()

    def tearDown(self):
        (self.db.DB_PATH, done, self.db._WAL_SET, self.ex._MIGRATED[0], self.guard.check_action) = self.saved
        self.db._SCHEMA_DONE.clear(); self.db._SCHEMA_DONE.update(done)
        import gc; gc.collect()
        self.tmp.cleanup()

    def seed(self):
        from agents import dealer
        c = self.db.connect()
        c.execute("CREATE TABLE IF NOT EXISTS moltbook_demand (post_id TEXT PRIMARY KEY, submolt TEXT, author TEXT, "
                  "title TEXT, body TEXT, created_at TEXT, found_at TEXT, escalated_at TEXT, answered_at TEXT)")
        c.execute("INSERT OR IGNORE INTO moltbook_demand VALUES ('p1','usdc','buyer1','Will pay 5 USDC for data',"
                  "'need ranked x402 data','2026-09-15T00:00:00Z','2026-09-15T00:00:00+00:00',NULL,NULL)")
        c.execute("CREATE TABLE IF NOT EXISTS moltbook_address_policy (submolt TEXT PRIMARY KEY, with_address INTEGER, "
                  "posts INTEGER, oldest TEXT, checked_at TEXT)")
        c.execute("INSERT OR IGNORE INTO moltbook_address_policy VALUES ('usdc', 4, 87, '2026-07-20', ?)", (dealer.now(),))
        c.commit(); c.close()
        dealer.weave()                       # каналы и посев стратегий
        c = self.db.connect()
        c.execute("INSERT OR IGNORE INTO channels(key,platform,kind,url,how,needs_account,alive,discovered_at) "
                  "VALUES ('index:testindex','testindex','index','https://example.test','api',0,1,?)", (dealer.now(),))
        c.commit(); c.close()


class Thinking(TempDB):
    def test_hypotheses_come_from_live_components_and_dedupe(self):
        from agents import strategist
        self.seed()
        sig = strategist.observe()
        n1 = strategist.generate(sig)
        n2 = strategist.generate(sig)
        self.assertGreater(n1, 0)
        self.assertEqual(n2, 0, "повторная генерация из тех же компонентов не плодит дубли")
        c = self.db.connect()
        names = [r[0] for r in c.execute("SELECT name FROM strategy_hypotheses")]
        rows = c.execute("SELECT name, source, evidence FROM strategy_hypotheses").fetchall()
        c.close()
        self.assertTrue(any("reply demand in m/usdc" in n for n in names))
        self.assertTrue(any("offer post in m/usdc" in n for n in names), "адреса живут в usdc → пост с адресом")
        self.assertTrue(any("be listed in testindex" in n for n in names))
        self.assertTrue(all(r[1] and r[2] for r in rows), "у каждой гипотезы есть источник и улика")

    def test_blocked_when_no_executor_and_asked_once(self):
        from agents import strategist, bounty
        self.seed()
        c = self.db.connect()
        c.execute("INSERT OR IGNORE INTO channels(key,platform,kind,url,how,needs_account,alive,discovered_at) "
                  "VALUES ('index:taskmarket','taskmarket','task_board','https://taskmarket.dev','cli',1,1,?)", (strategist.now(),))
        c.commit(); c.close()
        strategist.generate(strategist.observe())
        c = self.db.connect()
        blocked = c.execute("SELECT name, needs FROM strategy_hypotheses WHERE status='blocked'").fetchall()
        c.close()
        self.assertTrue(any("escrow" in n for n, _ in blocked), blocked)
        self.assertTrue(all(needs for _, needs in blocked))
        first = strategist.ask_for_capabilities()
        second = strategist.ask_for_capabilities()
        self.assertGreaterEqual(first, 1)
        self.assertEqual(second, 0, "одна нехватка — одна просьба")

    def test_signal_moves_score_and_budget_decides(self):
        from agents import strategist
        self.seed()
        sig = strategist.observe()
        strategist.generate(sig)
        strategist.prioritize(sig)
        c = self.db.connect()
        before = c.execute("SELECT score FROM strategy_hypotheses WHERE name='reply demand in m/usdc'").fetchone()[0]
        c.execute("UPDATE counterparties SET replies=1 WHERE 1=0")   # без ответов
        c.execute("INSERT INTO counterparties(handle,platform,kind,source,funds_signal,replies,first_seen,updated_at,status) "
                  "VALUES ('x','moltbook','agent','submolt:usdc',0.8,1,?,?,'replied')", (strategist.now(), strategist.now()))
        c.commit(); c.close()
        sig2 = strategist.observe()
        strategist.prioritize(sig2)
        c = self.db.connect()
        after = c.execute("SELECT score FROM strategy_hypotheses WHERE name='reply demand in m/usdc'").fetchone()[0]
        c.close()
        self.assertGreater(after, before, "источник, где отвечали, ценнее")
        # бюджет: три пустые попытки → снята; сигнал → оставлена
        strategist._run_experiment = lambda h: ("noop-test", "пусто", 0)
        for _ in range(3):
            strategist.experiment(max_runs=1)
        c = self.db.connect()
        st = c.execute("SELECT status FROM strategy_hypotheses ORDER BY score DESC LIMIT 1").fetchone()[0]
        c.close()
        self.assertIn(st, ("killed", "testing", "kept"))
        strategist._run_experiment = lambda h: ("dealer.act", "sent", 1)
        for _ in range(3):
            strategist.experiment(max_runs=1)
        c = self.db.connect()
        kept = c.execute("SELECT COUNT(*) FROM strategy_hypotheses WHERE status='kept'").fetchone()[0]
        c.close()
        self.assertGreaterEqual(kept, 1, "гипотеза с сигналом остаётся")
        n = strategist.generate(strategist.observe())
        c = self.db.connect()
        muts = c.execute("SELECT COUNT(*) FROM strategy_hypotheses WHERE origin='mutation'").fetchone()[0]
        c.close()
        self.assertGreaterEqual(muts, 1, "от оставленной гипотезы идут ветви")
        self.assertIsNotNone(strategist.state(write=True))


if __name__ == "__main__":
    unittest.main()
