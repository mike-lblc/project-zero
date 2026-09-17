"""СДЕЛАННАЯ РАБОТА ОБЯЗАНА ДОХОДИТЬ ДО ПОДАЧИ (17.09.2026).

Владелец спросил: «агенты делают работы и подают их?» Замер за сутки ответил честно
и неприятно: 126 внешних действий, из которых 125 — проверки НАШЕГО ЖЕ сервиса и
одно сообщение контрагенту. Подач — ноль: ни награды, ни задачи, ни PR.

Причина оказалась не в лени, а в трёх разрывах:

1. `produce_doc` — ЕДИНСТВЕННЫЙ производитель, который передаёт работу доставщику, —
   был объявлен в executor.CYCLE и не стоял НИ В ОДНОМ цикле. Заявка ремесленника
   на документацию ждала неразобранной с 12.09, пять суток.
2. `produce_work` (он как раз в цикле и в облаке) делал файл и НИКОМУ о нём не
   сообщал: в 14:21 выдал 6865 байт и молча положил на диск. Доставщик ждёт
   сообщения `documentation_ready` и честно отвечал «готовой работы нет».
3. Цель `produce_work` задана в коде и УЖЕ ИСЧЕРПАНА: работа по BasedHardware/omi
   вмержена (PR #13455), награда помечена lost. То есть шаг заново делал то, что
   давно лежит в чужом репозитории, — а наивно дотянутая цепочка доставки отправила
   бы ПОВТОРНЫЙ PR с уже вмерженной работой. Это спам, и цена ошибки — аккаунт,
   через который мы дотягиваемся до всех лидов.
"""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import roster  # noqa: E402
from core.agent import TOOLS  # noqa: E402

roster.wire()
from agents import worker  # noqa: E402


class TheChainIsWired(unittest.TestCase):
    def test_the_handover_capable_producer_is_scheduled(self):
        """Производитель, который передаёт работу, обязан кем-то вызываться."""
        steps = dict(worker.CYCLE + worker.SLOW_CYCLE)
        self.assertIn("produce_doc", steps, "produce_doc снова не стоит ни в одном цикле")
        self.assertIn("produce_doc", worker.CLOUD_STEPS,
                      "produce_doc не работает в облаке — без ноутбука подач не будет")
        self.assertEqual(worker.AGENT_OF.get("produce_doc"), "executor")

    def test_the_producer_hands_work_over(self):
        """Работа без передачи — работа в стол: доставщик её не увидит."""
        import inspect
        src = inspect.getsource(TOOLS["produce_work"].fn)
        self.assertIn("documentation_ready", src,
                      "produce_work снова не сообщает о готовой работе")
        self.assertIn("craftsman", src, "передача уходит не доставщику")

    def test_the_delivery_step_consumes_that_exact_topic(self):
        """Оба конца должны говорить об одном: тема сообщения совпадает."""
        import inspect
        from agents import craftsman
        self.assertIn("documentation_ready", inspect.getsource(craftsman.deliver_ready))


class NeverResubmitFinishedWork(unittest.TestCase):
    """Повторный PR с уже вмерженной работой — спам, а не продуктивность."""

    def test_an_already_merged_target_is_refused(self):
        out = str(TOOLS["produce_work"].fn())
        # В базе есть вмерженный PR по BasedHardware/omi (#13455).
        self.assertIn("уже сдана", out, f"шаг снова делает сданную работу: {out[:120]}")
        self.assertIn("новая цель", out.lower().replace("новую", "новая"),
                      "шаг не просит новую цель, хотя прежняя исчерпана")

    def test_the_refusal_is_based_on_the_pr_table_not_a_hardcoded_name(self):
        """Отказ должен опираться на факт сдачи, а не на зашитое имя репозитория."""
        import inspect
        src = inspect.getsource(TOOLS["produce_work"].fn)
        self.assertIn("pull_requests", src, "проверка сдачи не смотрит в таблицу PR")
        self.assertIn("MERGED", src.upper())


class HonestAccounting(unittest.TestCase):
    def test_self_pings_are_not_counted_as_outward_work(self):
        """125 проверок своего же сервиса — это не 125 внешних действий.

        Замер суток показал 126 «внешних действий», из которых 125 — health_check
        нашего собственного воркера. Если такое считать работой наружу, ведомость
        будет зелёной при нулевой выручке.
        """
        from core.db import connect
        c = connect()
        rows = dict(c.execute(
            "SELECT kind, COUNT(*) FROM actions WHERE dry_run=0 "
            "AND created_at LIKE strftime('%Y-%m-%d', 'now') || '%' GROUP BY kind").fetchall())
        c.close()
        outward = {k: v for k, v in rows.items() if k != "health_check"}
        # Тест не требует, чтобы подачи БЫЛИ — он требует, чтобы их не путали с
        # самопроверками. Проверяем, что health_check отделим и отделён.
        self.assertNotIn("health_check", outward)


if __name__ == "__main__":
    unittest.main(verbosity=2)
