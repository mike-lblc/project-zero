"""КАЖДЫЙ АГЕНТ ПОЛУЧАЕТ СЛОВО, И КАЖДЫЙ ШАГ ИМЕЕТ ХОЗЯИНА.

Что было 13.09.2026. Очередь рассуждающих агентов жила в памяти процесса и при
каждом из 35 перезапусков за сутки начиналась с начала алфавита: у adversary
девять решений, у supplier, verifier и watchdog — по одному за двадцать часов.
Четырнадцать шагов цикла писались в журнал как orchestrator, и исполнитель,
улучшатель и снабженец выглядели молчащими при настоящей работе.
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class Fairness(unittest.TestCase):
    def setUp(self):
        from core import db
        self.db = db
        self.saved = (db.DB_PATH, set(db._SCHEMA_DONE), db._WAL_SET)
        self.tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(self.tmp.name) / "fair.db"
        db._SCHEMA_DONE.clear()
        db._WAL_SET = False
        db.init().close()
        from core import agent
        agent._con().close()

    def tearDown(self):
        self.db.DB_PATH, done, self.db._WAL_SET = self.saved
        self.db._SCHEMA_DONE.clear()
        self.db._SCHEMA_DONE.update(done)
        self.tmp.cleanup()

    def decide(self, name, at):
        c = self.db.connect()
        c.execute("INSERT INTO agent_decisions(agent,state_seen,chose,why,allowed,outcome,ok,model,decided_at) "
                  "VALUES (?,?,?,?,?,?,?,?,?)", (name, "{}", "x", "", 1, "", 1, "m", at))
        c.commit(); c.close()

    def test_agent_that_never_decided_goes_first_regardless_of_alphabet(self):
        from agents import worker
        self.decide("adversary", "2026-09-13T10:00:00")
        self.decide("bounty", "2026-09-13T11:00:00")
        self.assertEqual(worker.next_reasoner(["adversary", "bounty", "watchdog"]), "watchdog")

    def test_longest_silent_agent_goes_next_after_restart(self):
        from agents import worker
        self.decide("adversary", "2026-09-13T12:00:00")
        self.decide("watchdog", "2026-09-12T18:00:00")
        # «перезапуск»: в памяти процесса ничего нет, решает журнал
        self.assertEqual(worker.next_reasoner(["adversary", "watchdog"]), "watchdog")


class StepOwners(unittest.TestCase):
    def test_every_cycle_step_has_an_explicit_owner(self):
        from agents import worker
        from core import roster  # noqa: F401
        from core.agent import REGISTRY
        steps = [n for n, _ in worker.CYCLE + worker.SLOW_CYCLE]
        missing = [s for s in steps if s not in worker.AGENT_OF]
        self.assertFalse(missing, f"шаги без хозяина пишутся как orchestrator: {missing}")
        owners = {}
        for name, a in REGISTRY.items():
            for t in a.tools:
                owners.setdefault(t, set()).add(name)
        wrong = {s: worker.AGENT_OF[s] for s in steps
                 if s in owners and worker.AGENT_OF[s] not in owners[s]}
        self.assertFalse(wrong, f"шаг приписан не владельцу своего инструмента: {wrong}")


if __name__ == "__main__":
    unittest.main()
