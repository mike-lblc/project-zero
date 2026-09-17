"""ЦИКЛ ПОВТОРНОГО ПРЕДЛОЖЕНИЯ — САМАЯ ДОРОГАЯ ТРАТА, ЗАКРЫТА (17.09.2026).

Адверсарный аудит (workflow wqzj9dfkq) замерил: шаг reason_and_act — 66% всего
машинного времени и 88% времени модели, и 84% его вызовов propose_path возвращали
«такая гипотеза уже есть в плане». Встроенный предохранитель повторов не срабатывал
на нём НИ РАЗУ, потому что ключевался по всей строке шага, а она всегда разная.

И вторая, тонкая: исполнитель гипотез красил ЛЮБУЮ listing-гипотезу как успех —
подстрока «registered» ловилась в чужом вердикте из общего отчёта. Так родились
17 ложных «kept» и десятки мутаций-мусора, которые агенты переписывали обратно.

Здесь закреплены обе починки — ровно в тех местах и с теми границами, которые
адверсарная проверка признала безопасными («nothing breaks», уверенность high).
"""
import json
import pathlib
import sys
import unittest
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import roster, agent as A  # noqa: E402

roster.wire()


def _log_decision(agent, chose, outcome, ok=1):
    c = A._con()
    c.execute("INSERT INTO agent_decisions(agent,state_seen,chose,why,allowed,outcome,ok,model,decided_at) "
              "VALUES (?,?,?,?,?,?,?,?,?)",
              (agent, "{}", chose, "x", 1, outcome, ok, "test", datetime.now(timezone.utc).isoformat()))
    c.commit(); c.close()


def _clear():
    c = A._con(); c.execute("DELETE FROM agent_decisions WHERE model='test'"); c.commit(); c.close()


class SpentToolSuppression(unittest.TestCase):
    def setUp(self):
        _clear()

    def tearDown(self):
        _clear()

    def test_a_tool_whose_last_outcome_was_empty_is_hidden(self):
        a = A.get("dealer")
        _log_decision("dealer", "propose_path", "такая гипотеза уже есть в плане")
        self.assertIn("propose_path", a._spent_tools())
        menu = a._prompt(a.state()).split("ПРЯМО СЕЙЧАС")[1].split("ТВОЁ ТЕКУЩЕЕ")[0]
        self.assertNotIn("propose_path", menu)

    def test_a_productive_outcome_brings_the_tool_back(self):
        """Узость: гасим только по ПОСЛЕДНЕМУ пустому исходу, не «выбирал недавно»."""
        a = A.get("dealer")
        _log_decision("dealer", "propose_path", "такая гипотеза уже есть в плане")
        _log_decision("dealer", "propose_path", "гипотеза добавлена в общий план")
        self.assertNotIn("propose_path", a._spent_tools())

    def test_decide_rejects_a_spent_tool_even_if_named(self):
        """Запрет, не только подсказка: allowed считается из self.tools, не из меню."""
        a = A.get("dealer")
        _log_decision("dealer", "note_finding", "замечаний нет")
        saved = A.router.run
        A.router.run = lambda k, p: json.dumps({"tool": "note_finding", "args": {"claim": "x"}, "why": "w"})
        try:
            out = a.decide()
        finally:
            A.router.run = saved
        self.assertFalse(out["allowed"])
        self.assertIn("пустой исход", out["why"])

    def test_suppression_is_per_agent(self):
        """Пустой исход у ОДНОГО агента не попадает в набор другого.

        Проверяем не «у другого пусто» (у него может быть свой настоящий пустой
        исход), а что вставка для dealer НЕ МЕНЯЕТ набор improver — то есть запрос
        читает строки строго по имени агента.
        """
        other = A.get("improver")
        before = other._spent_tools()
        _log_decision("dealer", "note_finding", "замечаний нет")
        after = other._spent_tools()
        self.assertEqual(before, after, "чужой пустой исход просочился в набор агента")


class ListingScoreHonesty(unittest.TestCase):
    """Исполнитель гипотез не имеет права красить чужой успех своим."""

    def test_platform_absent_from_report_scores_zero(self):
        from agents import strategist
        saved = strategist.__dict__.get("_dealer_seek")
        # подделываем отчёт: нашей площадки в нём НЕТ, но есть чужое 'registered'
        import agents.dealer as dealer
        real = dealer.seek_indexes
        dealer.seek_indexes = lambda discover=False: {
            "x402scan": "registered 11/14", "bazaar": "нас нет: не найден"}
        try:
            h = {"mechanism": "listing", "channel": "index_api",
                 "source": "index:mission69b", "offer": "x"}
            action, outcome, sig = strategist._run_experiment(h)
        finally:
            dealer.seek_indexes = real
        self.assertEqual(action, "dealer.seek_indexes")
        self.assertEqual(sig, 0, "чужое 'registered' не должно засчитываться нам")

    def test_platforms_own_registered_verdict_scores_one(self):
        from agents import strategist
        import agents.dealer as dealer
        real = dealer.seek_indexes
        dealer.seek_indexes = lambda discover=False: {
            "x402scan": "registered 11/14", "bazaar": "нас нет"}
        try:
            h = {"mechanism": "listing", "channel": "index_api",
                 "source": "index:x402scan", "offer": "x"}
            action, outcome, sig = strategist._run_experiment(h)
        finally:
            dealer.seek_indexes = real
        self.assertEqual(sig, 1, "собственный вердикт площадки 'registered' = успех")

    def test_platforms_own_negative_verdict_scores_zero(self):
        from agents import strategist
        import agents.dealer as dealer
        real = dealer.seek_indexes
        dealer.seek_indexes = lambda discover=False: {"bazaar": "нас нет: не найден в ленте"}
        try:
            h = {"mechanism": "listing", "channel": "index_api",
                 "source": "index:bazaar", "offer": "x"}
            action, outcome, sig = strategist._run_experiment(h)
        finally:
            dealer.seek_indexes = real
        self.assertEqual(sig, 0)


class GenerationGate(unittest.TestCase):
    def test_only_confirmed_indexes_mint_listing_hypotheses(self):
        """index_candidate (репозиторий GitHub с рыночным именем) не порождает 'be listed'."""
        import inspect
        from agents import strategist
        src = inspect.getsource(strategist.generate)
        self.assertIn('kind == "index"', src)
        self.assertNotIn('kind in ("index", "index_candidate")', src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
