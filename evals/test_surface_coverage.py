"""ВИТРИНА ОСМАТРИВАЕТСЯ ЦЕЛИКОМ, А НЕ ПЕРВОЙ ЕЁ ШЕСТОЙ ЧАСТЬЮ (18.09.2026).

`validate_surface` проверяет наши маршруты ЧУЖИМИ воротами Coinbase — это
единственная бесплатная проверка «примет ли нас сторона покупателя». Предел в
ней стоял 11 с тех времён, когда маршрутов и было одиннадцать. Маршрутов стало
62, а шаг молча осматривал первые 11 по алфавиту: сломанный маршрут где-нибудь
на букве «s» не нашёл бы никто, и мы бы узнали о нём от неполучившего товар
покупателя.

Здесь закреплено два свойства: окно едет по кругу (за несколько ходов витрина
осматривается вся) и шаг больше не повторяет опровергнутую причину отсутствия
в ленте обнаружения.
"""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agents import sell_surface  # noqa: E402


class Rotation(unittest.TestCase):
    def setUp(self):
        self.cursor = ROOT / "data" / "surface_cursor.txt"
        self.saved = self.cursor.read_text(encoding="utf-8") if self.cursor.exists() else None

    def tearDown(self):
        if self.saved is not None:
            self.cursor.write_text(self.saved, encoding="utf-8")
        elif self.cursor.exists():
            self.cursor.unlink()

    def test_window_moves_and_covers_everything(self):
        """Четыре хода по 16 обязаны покрыть все 62 маршрута без дыр."""
        total, step = 62, 16
        self.cursor.write_text("0", encoding="utf-8")
        seen = set()
        for _ in range((total + step - 1) // step):
            cur = sell_surface._rotating_cursor(total, step)
            seen.update((cur + i) % total for i in range(step))
        self.assertEqual(len(seen), total,
                         f"витрина осмотрена не вся: {total - len(seen)} маршрутов не проверялись")

    def test_a_single_run_does_not_hammer_the_free_endpoint(self):
        """Осматриваем окном, а не всеми шестьюдесятью запросами разом."""
        import inspect
        sig = inspect.signature(sell_surface.validate_surface)
        self.assertLessEqual(sig.parameters["limit"].default, 24,
                             "слишком большое окно за один ход по чужому бесплатному эндпоинту")
        self.assertGreater(sig.parameters["limit"].default, 11,
                           "окно осталось прежним — витрина по-прежнему осматривается частично")


class NoRefutedClaim(unittest.TestCase):
    def test_the_step_no_longer_repeats_the_disproved_reason(self):
        """Опровергнутая причина не должна возвращаться в отчёт агента.

        Утверждение «в ленту CDP пускают только после платежа через фасилитатор»
        проверено и неверно: лента выкачана целиком (15 625 записей), и в ней есть
        продавцы с нулём переводов за всю жизнь. Ложная причина увела работу в
        discovery, пока настоящая поломка — отказ заплатившему на 429 индексатора —
        жила незамеченной. Проверяем ИСПОЛНЯЕМЫЙ код, а не комментарии: в
        пояснениях эта фраза обязана остаться как история ошибки.
        """
        src = (ROOT / "agents" / "sell_surface.py").read_text(encoding="utf-8")
        code = "\n".join(ln.split("#")[0] for ln in src.splitlines())
        for ln in code.splitlines():
            bad = "нужен ОДИН платёж" in ln or "нужен один платёж" in ln
            self.assertFalse(bad, f"опровергнутая причина снова в коде: {ln.strip()[:80]}")




class DeclaredRouteCap(unittest.TestCase):
    """Предел на число объявленных маршрутов обязан быть ВЫШЕ факта.

    18.09 предел стоял 40, а маршрутов агенты накопили 51 — и хранилище
    заперлось: POST /routes отвечал «at most 40 declared routes» на любую запись.
    Значит нельзя было ни объявить новый маршрут, ни ПОПРАВИТЬ ЦЕНУ. А
    расхождение объявленной и живой цены каталог считает price_drift и валит
    маршрут: в тот момент дрейфовало двенадцать наших объявлений, и починить их
    было нечем. Предел ниже факта — это не защита, это замок изнутри.
    """

    def test_the_cap_is_above_what_we_already_declared(self):
        import json
        import re
        src = (ROOT / "worker" / "src" / "index.js").read_text(encoding="utf-8")
        m = re.search(r"list\.length > (\d+)\) return json\(\{ error: \"at most", src)
        self.assertIsNotNone(m, "предел объявленных маршрутов больше не находится в коде")
        cap = int(m.group(1))
        baked = json.loads((ROOT / "worker" / "routes.json").read_text(encoding="utf-8"))
        have = len(baked.get("routes") or [])
        self.assertGreater(cap, have,
                           f"предел {cap} не выше уже объявленных {have}: хранилище заперто, "
                           f"цену не поправить и маршрут не добавить")

if __name__ == "__main__":
    unittest.main(verbosity=2)
