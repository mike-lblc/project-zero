"""ЦИКЛ НЕ ЗАСТРЕВАЕТ НА СОБЫТИИ, ЧЕЙ ОБРАБОТЧИК НА ПАУЗЕ.

Что было 13.09.2026. В очереди лежало одно событие invariant_broken, его
обработчик — механик — стоял на паузе восемьдесят минут. Цикл брал событие,
шаг пропускался из-за паузы, событие возвращалось в очередь и тут же бралось
снова. Ни один обычный шаг не выполнялся; сторож видел молчащий журнал и
перезапускал воркер каждые пять минут.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class PausedHandler(unittest.TestCase):
    def test_event_with_paused_handler_is_not_taken(self):
        from agents import worker
        from core import events
        claimed = []
        saved = (events.pending, events.claim, worker.should_run)
        events.pending = lambda limit=1: [{"id": 7, "kind": "invariant_broken", "priority": 2}]
        events.claim = lambda eid, agent: claimed.append(eid) or True
        worker.should_run = lambda name: False
        try:
            self.assertIsNone(worker._take_event())
            self.assertEqual(claimed, [], "событие взято, хотя его обработчик на паузе")
        finally:
            events.pending, events.claim, worker.should_run = saved

    def test_event_with_active_handler_is_taken(self):
        from agents import worker
        from core import events
        saved = (events.pending, events.claim, worker.should_run)
        events.pending = lambda limit=1: [{"id": 7, "kind": "invariant_broken", "priority": 2}]
        events.claim = lambda eid, agent: True
        worker.should_run = lambda name: True
        try:
            taken = worker._take_event()
            self.assertEqual(taken[0], "mechanic")
        finally:
            events.pending, events.claim, worker.should_run = saved
            worker._PENDING_EVENT[0] = None


if __name__ == "__main__":
    unittest.main()
