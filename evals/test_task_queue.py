"""ОЧЕРЕДЬ ЗАДАЧ НЕ РАЗМНОЖАЕТСЯ.

Что было. Часовой аудит переводил зависшую задачу в провал и назначал новую
попытку. Провал порождал копию, а очередь поднимала И упавшего родителя, И
копию. Каждые шесть часов задач становилось вдвое больше: девять целей за двое
суток превратились в тридцать восемь задач, восемь из них — про премию, которую
владелец велел отменить. Каждый старт слал владельцу отдельную эскалацию: их
накопилось шестьдесят пять.

База временная: живую очередь тест не трогает.
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class Queue(unittest.TestCase):
    def setUp(self):
        from core import db
        self.db = db
        self.saved = (db.DB_PATH, set(db._SCHEMA_DONE), db._WAL_SET)
        self.tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(self.tmp.name) / "q.db"
        db._SCHEMA_DONE.clear()
        db._WAL_SET = False
        db.init().close()
        from core import execution
        self.ex = execution

    def tearDown(self):
        self.db.DB_PATH, done, self.db._WAL_SET = self.saved
        self.db._SCHEMA_DONE.clear()
        self.db._SCHEMA_DONE.update(done)
        self.tmp.cleanup()

    def active(self, objective):
        c = self.db.connect()
        n = c.execute("SELECT COUNT(*) FROM tasks WHERE objective=? AND state NOT IN "
                      "('done','cancelled','REJECTED','FAILED')", (objective,)).fetchone()[0]
        c.close()
        return n

    def test_duplicate_objective_returns_existing(self):
        a = self.ex.create("цель", "шаг", "orchestrator")
        b = self.ex.create("цель", "другой шаг", "orchestrator")
        self.assertEqual(a, b)

    def test_failed_parent_with_successor_is_closed(self):
        a = self.ex.create("цель", "шаг", "orchestrator")
        self.ex.start(a)
        child = self.ex.fail(a, "висела", next_action="разобрать")
        self.assertNotEqual(child, a)
        c = self.db.connect()
        state = c.execute("SELECT state FROM tasks WHERE id=?", (a,)).fetchone()[0]
        c.close()
        self.assertEqual(state, "cancelled")
        self.assertEqual(self.ex.next_task()["id"], child)

    def test_audit_and_queue_loop_does_not_multiply(self):
        """Пять оборотов петли — и по-прежнему одна живая задача на цель."""
        for obj in ("премия", "канал", "подписчики"):
            self.ex.create(obj, "шаг", "orchestrator")
        for _ in range(5):
            # очередь берёт в работу всё, что может
            while True:
                t = self.ex.next_task()
                if not t:
                    break
                c = self.db.connect()
                st = c.execute("SELECT state FROM tasks WHERE id=?", (t["id"],)).fetchone()[0]
                c.close()
                if st == "failed":
                    self.ex.unblock(t["id"], "новая попытка")
                self.ex.start(t["id"], "взята")
            # аудит валит всё зависшее с новой попыткой
            c = self.db.connect()
            running = [r[0] for r in c.execute("SELECT id FROM tasks WHERE state='running'")]
            c.close()
            for tid in running:
                self.ex.fail(tid, "висела", next_action="разобрать")
        for obj in ("премия", "канал", "подписчики"):
            self.assertEqual(self.active(obj), 1, f"цель «{obj}» размножилась")


if __name__ == "__main__":
    unittest.main()
