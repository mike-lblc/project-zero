"""ОБЛАКО РАБОТАЕТ НЕПРЕРЫВНО, А НЕ ШЕСТЬ РАЗ В СУТКИ (17.09.2026).

Владелец: «WHY NO MORE NEW TRANSACTIONS? ENSURE AI AGENTS ECOSYSTEM IS ALWAYS
WORKING». Прямая причина нашлась в истории запусков GitHub: в расписании стоит
«каждые 15 минут» (96 прогонов в сутки), фактически запускалось ~6 — медиана
промежутка 200 минут, худший 347. Облачный цикл был одноразовым, поэтому без
ноутбука владельца денежные шаги шли шесть раз в сутки по одному обороту.

Теперь один прогон живёт пять часов и крутит ТОТ ЖЕ цикл, что локальная машина.
Здесь закреплено то, на чём это чуть не сломалось: предел по времени, строгость
фильтра шагов и пустая очередь редких шагов.
"""
import pathlib
import sys
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agents import worker  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        # Замок живого локального воркера не трогаем: тест не должен ни
        # останавливать его, ни притворяться им.
        self.saved_lock = worker.LOCK
        worker.LOCK = pathlib.Path(tempfile.mkdtemp()) / "test.lock"
        self.saved = (list(worker.CYCLE), list(worker.SLOW_CYCLE), list(worker.MONEY_CYCLE))

    def tearDown(self):
        worker.LOCK = self.saved_lock
        worker.CYCLE, worker.SLOW_CYCLE, worker.MONEY_CYCLE = self.saved


class Deadline(Base):
    def test_run_stops_at_the_deadline(self):
        """Прогон обязан остановиться сам: иначе его убьёт таймаут раньше публикации.

        Пауза повторов здесь отключена НАРОЧНО. Первая версия теста считала вызовы
        шага и падала: цикл честно сделал 15 оборотов, но сам шаг не вызвался ни
        разу — его увела на паузу защита от повторов, потому что предыдущий прогон
        теста записал те же результаты. Это правильное поведение системы, и мерить
        им предел по времени нельзя.
        """
        calls = []
        worker.CYCLE = [("t", lambda: calls.append(1) or "ok")]
        worker.SLOW_CYCLE = []
        worker.MONEY_CYCLE = []
        saved_should = worker.should_run
        worker.should_run = lambda name: True
        try:
            t0 = time.time()
            worker.run_forever(interval=1, deadline_minutes=0.05, only={"t"})
            spent = time.time() - t0
        finally:
            worker.should_run = saved_should
        self.assertGreater(len(calls), 0, "цикл не выполнил ни одного оборота")
        # 3 секунды предела плюс один последний шаг — но не минуты.
        self.assertLess(spent, 60, f"цикл не остановился по пределу: {spent:.0f}с")

    def test_repeat_backoff_still_pauses_a_useless_step(self):
        """Пауза повторов — это и есть встроенная защита от траты; она обязана работать.

        Замечена в бою на этом же тесте: шаг, возвращающий одно и то же, перестаёт
        вызываться, хотя обороты продолжаются. Владелец просил убрать трату — вот
        механизм, который её убирает, и он не должен тихо отключиться.
        """
        import inspect
        self.assertIn("should_run", inspect.getsource(worker._turn),
                      "оборот больше не спрашивает разрешения — пауза повторов обойдена")

    def test_without_deadline_it_is_still_endless(self):
        """Локальная работа не должна получить предел случайно."""
        import inspect
        sig = inspect.signature(worker.run_forever)
        self.assertIsNone(sig.parameters["deadline_minutes"].default)
        self.assertIsNone(sig.parameters["only"].default)


class StrictFilter(Base):
    def test_filter_is_not_bypassed_by_the_thinking_slot(self):
        """Слот мышления обходил фильтр и поднимал шаг, которого в облаке нет.

        При only={t} цикл всё равно вызывал reason_and_act, а тот дёргал
        инструменты, которым нужна локальная машина. Фильтр, обходимый изнутри,
        фильтром не является.
        """
        thought = []
        worker.CYCLE = [("t", lambda: "ok")]
        worker.SLOW_CYCLE = []
        worker.MONEY_CYCLE = []
        saved = worker.reason_and_act
        worker.reason_and_act = lambda *a, **k: thought.append(1) or "думал"
        try:
            # Оборотов заведомо больше, чем THINK_AT: слот успел бы сработать.
            worker.run_forever(interval=1, deadline_minutes=0.12, only={"t"})
        finally:
            worker.reason_and_act = saved
        self.assertEqual(thought, [], "слот мышления пробился через фильтр")

    def test_empty_slow_queue_does_not_crash_the_loop(self):
        """Пустая очередь редких шагов роняла цикл делением на ноль.

        Упавший облачный цикл — это ровно та тишина, которую мы лечим, поэтому
        крах здесь страшнее любой потерянной работы.
        """
        worker.CYCLE = [("t", lambda: "ok")]
        worker.SLOW_CYCLE = []
        worker.MONEY_CYCLE = []
        worker.run_forever(interval=1, deadline_minutes=0.08, only={"t"})   # не должно бросить


class Wiring(unittest.TestCase):
    def test_cloud_workflow_runs_the_continuous_loop(self):
        """Облачный файл обязан звать тот же цикл, а не второй его вариант."""
        wf = (ROOT / ".github" / "workflows" / "agents.yml").read_text(encoding="utf-8")
        self.assertIn("run_forever(", wf, "облако не зовёт общий цикл")
        self.assertIn("deadline_minutes", wf, "у облачного прогона нет предела по времени")
        self.assertIn("only=set(worker.CLOUD_STEPS)", wf, "облако не ограничено облачными шагами")

    def test_timeout_leaves_room_after_the_loop(self):
        """Предел цикла обязан быть меньше таймаута задания: иначе дашборд не опубликуется."""
        import re
        wf = (ROOT / ".github" / "workflows" / "agents.yml").read_text(encoding="utf-8")
        timeout = int(re.search(r"timeout-minutes:\s*(\d+)", wf).group(1))
        minutes = int(re.search(r"P0_CLOUD_MINUTES:\s*'(\d+)'", wf).group(1))
        self.assertLess(minutes, timeout, "цикл займёт весь таймаут — публикация не успеет")
        self.assertGreaterEqual(timeout - minutes, 30,
                                "запаса после цикла меньше 30 минут")

    def test_money_steps_are_available_to_the_cloud(self):
        """Денежные шаги обязаны быть в облачном списке, иначе облако не зарабатывает."""
        for step in ("watch_payments", "sell_surface", "directory_watch"):
            self.assertIn(step, worker.CLOUD_STEPS, f"{step} не работает в облаке")


if __name__ == "__main__":
    unittest.main(verbosity=2)
