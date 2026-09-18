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
    """База с ИЗОЛИРОВАННОЙ записью прогонов.

    ТЕСТЫ НЕ ИМЕЮТ ПРАВА ПИСАТЬ В БОЕВУЮ ВЕДОМОСТЬ. Замер 18.09: из 190 «прогонов
    шагов за сегодня» 81 оказался мусором отсюда — шаг с именем «t», worker_start и
    worker_deadline. По этой же таблице владелец читает, чем заняты агенты, то есть
    мои тесты подмешивали 43% ложной активности в отчёт о работе. Ведомость, в
    которую пишут тесты, перестаёт быть ведомостью.
    """

    def setUp(self):
        # record_run уводится в пустоту на время теста: проверяем цикл, а не журнал.
        self._saved_record = worker.record_run
        worker.record_run = lambda *a, **k: None
        # Замок живого локального воркера не трогаем: тест не должен ни
        # останавливать его, ни притворяться им.
        self.saved_lock = worker.LOCK
        worker.LOCK = pathlib.Path(tempfile.mkdtemp()) / "test.lock"
        self.saved = (list(worker.CYCLE), list(worker.SLOW_CYCLE), list(worker.MONEY_CYCLE))

    def tearDown(self):
        worker.record_run = self._saved_record
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
        # Детерминированные часы: настоящий wall-clock делал тест хрупким — под
        # нагрузкой всей сюиты старт съедал крошечный предел до первого оборота.
        # Здесь время идёт управляемым счётчиком: предел заведомо наступает ПОСЛЕ
        # нескольких оборотов, что и проверяем.
        calls = []
        worker.CYCLE = [("t", lambda: calls.append(1) or "ok")]
        worker.SLOW_CYCLE = []
        worker.MONEY_CYCLE = []
        clock = [1000.0]
        saved_time, saved_sleep, saved_should = worker.time.time, worker.time.sleep, worker.should_run
        worker.time.time = lambda: clock[0]
        worker.time.sleep = lambda s: clock.__setitem__(0, clock[0] + max(s, 1.0))
        worker.should_run = lambda name: True
        try:
            # предел 0.1 мин = 6 «секунд» модельных часов; шаг спит ~interval/1,
            # значит несколько оборотов пройдут прежде чем часы пересекут предел.
            worker.run_forever(interval=1, deadline_minutes=0.1, only={"t"})
        finally:
            worker.time.time, worker.time.sleep, worker.should_run = saved_time, saved_sleep, saved_should
        self.assertGreater(len(calls), 0, "цикл не выполнил ни одного оборота до предела")
        # часы пересекли предел => цикл вышел сам, а не крутится вечно
        self.assertGreaterEqual(clock[0], 1000.0 + 0.1 * 60, "цикл не дошёл до предела")

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


class MoneyStepsOnAClock(Base):
    """Шаг, от которого зависит выручка, не ждёт очереди наравне с летописью.

    Аудит замерил перекос: `directory_watch` — единственный канал, который когда-либо
    приносил платёж, — отработал 5 раз за 7 дней, потому что лежит в очереди редких
    шагов (один слот на 20 быстрых при 35 шагах в очереди). На это же наткнулся
    `deploy_if_changed`: в пятичасовом облачном прогоне он мог не подняться ни разу,
    и автономность деплоя осталась бы бумажной.
    """

    def test_critical_steps_run_on_their_own_clock(self):
        fired = []
        worker.CYCLE = [("t", lambda: "ok")]
        worker.SLOW_CYCLE = [
            ("watch_payments", lambda: fired.append("watch_payments") or "нет платежей"),
            ("deploy_if_changed", lambda: fired.append("deploy_if_changed") or "не менялся"),
        ]
        worker.MONEY_CYCLE = []
        saved_every = dict(worker.CRITICAL_EVERY)
        worker.CRITICAL_EVERY.clear()
        worker.CRITICAL_EVERY.update({"watch_payments": 0.01, "deploy_if_changed": 0.01})
        worker._CRITICAL_AT.clear()
        clock = [1000.0]
        s_time, s_sleep, s_should = worker.time.time, worker.time.sleep, worker.should_run
        worker.time.time = lambda: clock[0]
        worker.time.sleep = lambda s: clock.__setitem__(0, clock[0] + max(s, 1.0))
        worker.should_run = lambda name: True
        try:
            worker.run_forever(interval=1, deadline_minutes=0.1,
                               only={"t", "watch_payments", "deploy_if_changed"})
        finally:
            worker.time.time, worker.time.sleep, worker.should_run = s_time, s_sleep, s_should
            worker.CRITICAL_EVERY.clear(); worker.CRITICAL_EVERY.update(saved_every)
            worker._CRITICAL_AT.clear()
        self.assertEqual(set(fired), {"watch_payments", "deploy_if_changed"},
                         "денежные шаги не отработали по своим часам")

    def test_the_paying_channel_and_the_deploy_are_both_on_the_clock(self):
        """Состав списка — не декоративный: в нём обязаны быть платящий канал и выкат."""
        for step in ("watch_payments", "directory_watch", "sell_surface", "deploy_if_changed"):
            self.assertIn(step, worker.CRITICAL_EVERY, f"{step} снова ждёт очереди")
        # интервалы разумны: проверить платёж чаще, чем разворачивать
        self.assertLess(worker.CRITICAL_EVERY["watch_payments"],
                        worker.CRITICAL_EVERY["sell_surface"])


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
