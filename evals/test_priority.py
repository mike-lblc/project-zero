"""ПРИОРИТЕТ ПО ФОРМУЛЕ GND §9 — и запреты, которые нельзя обойти правкой.

expected_net_value × probability_of_acceptance × payout_reachability
÷ time_to_cash ÷ execution_cost ÷ competition
"""
import inspect
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import priority as P  # noqa: E402


def est(amount=50, reachable=True, declared=True, rivals=0, contest=False):
    return P.bounty_estimate(amount, reachable, declared, trust=0.6, stack_fit=1.0,
                             rivals=rivals, comments=0, is_contest=contest)


class Formula(unittest.TestCase):
    def test_no_currency_or_network_can_enter_the_score(self):
        """Бонус за крипту или за готовый адаптер Base нельзя даже передать."""
        fields = set(P.Estimate.__dataclass_fields__)
        banned = {"currency", "network", "chain", "provider", "asset", "crypto", "adapter"}
        self.assertFalse(fields & banned, f"в оценке появились запрещённые поля: {fields & banned}")
        params = set(inspect.signature(P.bounty_estimate).parameters)
        self.assertFalse(params & banned, f"в оценку передаётся запрещённое: {params & banned}")

    def test_formula_is_the_directive_formula(self):
        e = P.Estimate(100, 0.5, 1.0, 2, 4, 1)
        self.assertAlmostEqual(P.score(e), 100 * 0.5 * 1.0 / 2 / 4 / 2)

    def test_small_quick_task_beats_big_slow_one(self):
        self.assertGreater(P.score(est(50)), P.score(est(500)))

    def test_unknown_payout_is_below_verified_and_above_impossible(self):
        self.assertGreater(P.score(est(reachable=True)), P.score(est(reachable=None)))
        self.assertGreater(P.score(est(reachable=None)), 0)
        self.assertEqual(P.score(est(reachable=False)), 0)

    def test_competition_lowers_priority(self):
        self.assertGreater(P.score(est(rivals=0)), P.score(est(rivals=3)))

    def test_undeclared_budget_is_heavily_discounted(self):
        self.assertGreater(P.score(est(declared=True)), 5 * P.score(est(declared=False)))

    def test_contest_prize_is_not_a_bounty(self):
        self.assertGreater(P.score(est(50)), P.score(est(10_000, reachable=None, contest=True)))

    def test_contest_is_scored_by_places_and_participants(self):
        """Фонд $740 000 на 25 510 участников — не $14 800 ожидания."""
        huge = P.bounty_estimate(740_000, None, True, 0.5, 0.5, 0, 0, is_contest=True,
                                 participants=25_510, prizes=1)
        small = P.bounty_estimate(66_000, None, True, 0.5, 0.5, 0, 0, is_contest=True,
                                  participants=77, prizes=9)
        self.assertLess(huge.expected_net_value * huge.probability_of_acceptance, 30)
        self.assertGreater(P.score(small), P.score(huge))

    def test_ties_follow_directive_order(self):
        a = P.Estimate(10, 1, 1, 1, 1, 0, budget_declared=True)
        b = P.Estimate(10, 1, 1, 1, 1, 0, budget_declared=False)
        self.assertEqual(sorted([b, a], key=P.tie_key)[0], a)


if __name__ == "__main__":
    unittest.main()
