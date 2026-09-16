"""ЦЕНА ОБЪЯВЛЕНА ОДИН РАЗ (16.09.2026).

Цены жили в трёх местах и разошлись с живой службой после переоценки: сторож охранял
«датасет за $1.25», который продаётся за $0.25, а советчик по ценам строил выводы от
/report $0.10 при живых $0.02. Никто не соврал — просто никто не пересчитал все три.

Источник истины — worker/src/index.js (TIERS). core/identity.TARIFF — его зеркало.
Этот тест падает, как только они разойдутся, и читает файл воркера, а не сеть:
проверка не должна зависеть от того, есть ли интернет.
"""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
WORKER = ROOT / "worker" / "src" / "index.js"


def deployed_tiers():
    """Тарифы, как они записаны в коде воркера: путь -> цена в долларах."""
    src = WORKER.read_text(encoding="utf-8")
    block = re.search(r"const TIERS = \{(.*?)\n\};", src, re.S)
    assert block, "в воркере не найден блок const TIERS"
    out = {}
    for path, usd in re.findall(r'"(/[a-z]+)":\s*\{[^}]*?usd:\s*([0-9.]+)', block.group(1)):
        out[path] = float(usd)
    return out


class Tariff(unittest.TestCase):
    def test_declared_matches_the_worker(self):
        from core.identity import TIERS
        self.assertEqual(TIERS, deployed_tiers(),
                         "core/identity.TIERS разошёлся с worker/src/index.js — "
                         "цена объявляется в одном месте")

    def test_every_route_has_a_price_above_zero(self):
        from core.identity import TARIFF
        for path, t in TARIFF.items():
            self.assertGreater(t["usd"], 0, f"{path} без цены")
            self.assertTrue(t["what"].strip(), f"{path} без описания")

    def test_guard_protects_at_the_selling_price(self):
        """Сторож охраняет товар по той цене, по которой он на самом деле продаётся."""
        from core import guard
        from core.identity import TIERS
        for name, p in guard.PAID_PRODUCTS.items():
            self.assertEqual(p["price"], TIERS[p["endpoint"]],
                             f"«{name}» охраняется по цене, которой нет в тарифе")

    def test_price_advisor_uses_the_same_numbers(self):
        from agents import team
        from core.identity import TIERS
        self.assertEqual(team.OUR_TIERS, TIERS)

    def test_paid_call_without_parameters_is_not_an_error(self):
        """Платный вызов без параметров обязан отдавать данные: за него уже заплатили.

        Это и есть класс отказов declared_params_unmet / rejected_400_blind_request,
        на котором каталог платящего покупателя теряет сотни эндпоинтов.
        """
        src = WORKER.read_text(encoding="utf-8")
        self.assertNotIn('error: "missing required parameter q"', src,
                         "/search снова отвечает ошибкой на оплаченный вызов без q")
        self.assertIn("top_by_demand", src, "нет ответа по умолчанию для вызова без q")

    def test_search_no_longer_declares_q_as_required(self):
        """Объявление возможностей не должно противоречить поведению."""
        src = WORKER.read_text(encoding="utf-8")
        block = re.search(r'"/search": \{\s*\n\s*method:.*?\n  \},', src, re.S)
        self.assertIsNotNone(block, "не найден блок INPUTS для /search")
        self.assertNotIn('required: ["q"]', block.group(0))


if __name__ == "__main__":
    unittest.main()
