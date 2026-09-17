"""ЗАБЛОКИРОВАННАЯ ИДЕЯ ДОЛЖНА ОЖИВАТЬ, КОГДА ПРИЧИНА ОТПАЛА (17.09.2026).

Владелец: «почему стратегии с нулём платежей не самоулучшаются?» Я сначала ответил
«их убивают вместо улучшения» — и это оказалось НЕВЕРНО. Замер:

  * организмом убито 0 гипотез; все 80 «killed» — моя же чистка ложных сигналов;
  * 186 из 223 гипотез не пробовались НИ РАЗУ (tries=0);
  * из 15 заблокированных СЕМЬ были связкой listing+x402scan, которую исполнитель
    научился делать несколькими часами ранее — но статус blocked терминален, и
    никто его не перечитывал.

То есть идеи не «убивались», а голодали: исполнимые лежали мёртвыми, потому что
система не замечала, что её собственные умения выросли. Здесь это закреплено.
"""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import roster  # noqa: E402

roster.wire()
from agents import strategist as st  # noqa: E402


class CanDispatchAgreesWithTheExecutor(unittest.TestCase):
    """Два ответа на один вопрос обязаны совпадать.

    `can_dispatch` отвечает «есть ли исполнитель» БЕЗ запуска, `_run_experiment` —
    запуском. Если они разойдутся, оживление вернёт в очередь то, что снова упадёт
    в noop и снова заблокируется — вечный цикл. Эту ошибку («две реализации одного
    вопроса») проект уже оплачивал, поэтому здесь она проверяется прямо.
    """

    def _action_for(self, mech, channel, source="index:x402scan"):
        """Что сделает исполнитель, не выходя в сеть."""
        import agents.dealer as dealer
        import agents.craftsman as craftsman
        import agents.bounty as bounty
        from core import moltbook
        saved = (dealer.seek_indexes, dealer.import_taskmarket, dealer.survey, dealer.model,
                 dealer.act, craftsman.pursue, bounty.deliver_aibtc, moltbook.create_post)
        dealer.seek_indexes = lambda discover=False: {"x402scan": "registered 1/1"}
        dealer.import_taskmarket = lambda: {"new": 0}
        dealer.survey = lambda: None
        dealer.model = lambda: None
        dealer.act = lambda dry_run=False, limit=1: {"sent": []}
        craftsman.pursue = lambda dry_run=False: "нет заявок"
        bounty.deliver_aibtc = lambda: "нет готовых"
        moltbook.create_post = lambda *a, **k: {"state": "SKIPPED"}
        try:
            action, _outcome, _sig = st._run_experiment(
                {"mechanism": mech, "channel": channel, "source": source, "offer": "x"})
            return action
        finally:
            (dealer.seek_indexes, dealer.import_taskmarket, dealer.survey, dealer.model,
             dealer.act, craftsman.pursue, bounty.deliver_aibtc, moltbook.create_post) = saved

    def test_the_two_answers_never_disagree(self):
        pairs = [
            ("listing", "index_api", "index:x402scan"), ("listing", "x402scan", "index:x402scan"),
            ("listing", "listing", "index:x402scan"), ("reciprocal", "index_api", "index:x402scan"),
            ("reciprocal", "moltbook_comment", "submolt:x402"),
            ("reply_demand", "moltbook_comment", "submolt:x402"),
            # offer_post требует ещё и сабмолт — с ним и без него
            ("offer_post", "moltbook_post", "submolt:x402"),
            ("offer_post", "moltbook_post", "index:x402scan"),
            ("board_import", "board_import", "board:taskmarket"),
            ("bounty_claim", "github_issue", "board:repo"),
            ("delivery", "aibtc_submit", "board:aibtc"),
            # заведомо НЕисполнимые
            ("escrow_claim", "taskmarket_cli", "board:taskmarket"),
            ("listing", "nohumans.directory", "index:nohumans"),
            ("offer_post", "direct", "submolt:x402"), ("listing", "Moltbook", "index:moltbook"),
        ]
        for mech, channel, source in pairs:
            promised = st.can_dispatch(mech, channel, source)
            actual = self._action_for(mech, channel, source) != "noop"
            self.assertEqual(promised, actual,
                             f"{mech}+{channel}+{source}: can_dispatch={promised}, исполнитель={actual}")


class Revival(unittest.TestCase):
    def test_an_executable_blocked_idea_comes_back(self):
        """Связка, которую исполнитель теперь умеет, возвращается в очередь."""
        c = st._con()
        c.execute("INSERT OR IGNORE INTO strategy_hypotheses"
                  "(name,origin,source,channel,offer,mechanism,rationale,status,needs,created_at,updated_at) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  ("__test revive me", "seed", "index:x402scan", "x402scan", "x", "listing",
                   "тест", "blocked", "нет исполнителя", st.now(), st.now()))
        c.commit(); c.close()
        st.revive_blocked()
        c = st._con()
        row = c.execute("SELECT status, needs FROM strategy_hypotheses WHERE name=?",
                        ("__test revive me",)).fetchone()
        c.execute("DELETE FROM strategy_hypotheses WHERE name=?", ("__test revive me",))
        c.commit(); c.close()
        self.assertEqual(row[0], "proposed", "исполнимая идея осталась заблокированной")
        self.assertIsNone(row[1], "причина блокировки не снята")

    def test_a_still_impossible_idea_stays_blocked(self):
        """Оживление не превращается в «разблокировать всё»: нехватка остаётся нехваткой."""
        c = st._con()
        c.execute("INSERT OR IGNORE INTO strategy_hypotheses"
                  "(name,origin,source,channel,offer,mechanism,rationale,status,needs,created_at,updated_at) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  ("__test keep blocked", "seed", "board:taskmarket", "taskmarket_cli", "x",
                   "escrow_claim", "тест", "blocked", "нужен кошелёк", st.now(), st.now()))
        c.commit(); c.close()
        st.revive_blocked()
        c = st._con()
        row = c.execute("SELECT status FROM strategy_hypotheses WHERE name=?",
                        ("__test keep blocked",)).fetchone()
        c.execute("DELETE FROM strategy_hypotheses WHERE name=?", ("__test keep blocked",))
        c.commit(); c.close()
        self.assertEqual(row[0], "blocked", "разблокировали то, что исполнить всё ещё нечем")

    def test_revival_runs_inside_the_thinking_cycle(self):
        """Оживление должно идти САМО и ДО попыток, иначе оно бесполезно."""
        import inspect
        src = inspect.getsource(st.think)
        self.assertIn("revive_blocked()", src, "оживление не вызывается в обороте мышления")
        self.assertLess(src.index("revive_blocked()"), src.index("experiment(max_runs"),
                        "оживление идёт после попыток — исполнимая идея ждёт лишний оборот")


if __name__ == "__main__":
    unittest.main(verbosity=2)
