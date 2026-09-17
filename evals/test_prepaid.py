"""ПЛАТЁЖ ПРИШЁЛ РАНЬШЕ ВЫЗОВА (17.09.2026).

В 09:35 нам заплатили двенадцать раз, 0.117 USDC, суммы точно по тарифу помаршрутно —
и воркер не записал ни одного paid. Платёжного заголовка не присылали вовсе: покупатель
перевёл цену прямо на payTo и позвал маршрут, а мы ответили 402. Деньги взяли, товар не
отдали; у каталога nohumans это класс paid_but_status_402, в него попадают 146 эндпоинтов.

Здесь стережётся сама развилка: поиск уже пришедшего платежа стоит ПЕРЕД показом цены,
и он не открыт для своих же проверок. Свойства самой функции проверяются в
worker/test/prepaid.test.mjs и запускаются отсюда же.
"""
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKER = ROOT / "worker" / "src" / "index.js"
JS_TEST = ROOT / "worker" / "test" / "prepaid.test.mjs"


class Wiring(unittest.TestCase):
    def setUp(self):
        self.src = WORKER.read_text(encoding="utf-8")

    def test_prepaid_is_tried_before_the_price_is_shown(self):
        """Иначе заплативший покупатель снова получит 402 вместо данных."""
        i_pre = self.src.find("const pre = await prepaid(")
        i_402 = self.src.find('const body402 =')
        self.assertGreater(i_pre, 0, "развилка prepaid не подключена")
        self.assertGreater(i_402, 0)
        self.assertLess(i_pre, i_402, "prepaid должен проверяться ДО показа цены")

    def test_our_own_probes_do_not_consume_payments(self):
        """Сторож и аудиты ничего не платили: им незачем тратить чужой перевод."""
        seg = self.src[self.src.find("const uaPre"):self.src.find("const pre = await prepaid(")]
        self.assertIn("P0-worker", seg)
        self.assertIn("P0-audit", seg)

    def test_one_transfer_is_spent_once(self):
        seg = self.src[self.src.find("async function prepaid("):self.src.find("async function directPaid(")]
        self.assertIn('"used:"', seg, "перевод не помечается использованным")
        self.assertIn("BASE_STABLES", seg, "токен не сверяется со списком признанных")
        self.assertIn("30 * 60 * 1000", seg, "нет окна свежести")
        self.assertIn("cand.sort", seg, "из подходящих должен браться самый дешёвый")

    def test_amount_must_cover_the_route_price(self):
        seg = self.src[self.src.find("async function prepaid("):self.src.find("async function directPaid(")]
        self.assertIn("tier.usd", seg, "цена маршрута не проверяется")


class Behaviour(unittest.TestCase):
    def test_js_properties_hold(self):
        """Восемь свойств функции: один перевод — один ответ, цент не открывает датасет и т.д."""
        node = shutil.which("node")
        if not node:
            self.skipTest("node не найден")
        r = subprocess.run([node, str(JS_TEST)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=300)
        self.assertEqual(r.returncode, 0, f"провал свойств prepaid:\n{r.stdout}\n{r.stderr}")
        self.assertIn("failed 0", r.stdout)


if __name__ == "__main__":
    unittest.main()
