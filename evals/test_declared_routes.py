"""МАРШРУТ, ОБЪЯВЛЕННЫЙ АГЕНТОМ, А НЕ ЧЕЛОВЕКОМ (17.09.2026).

Владелец разрешил агентам делать всё, что делал человек, включая добавление платных
маршрутов. Писать произвольный JS и деплоить платный сервис агенту НЕ дано: одна
ошибка в обработчике отдаёт данные бесплатно или роняет выручку в ноль. Вместо этого
маршрут — данные: путь, цена, описание и запрос из закрытого списка операций, который
воркер ИНТЕРПРЕТИРУЕТ.

Свойства движка (включая обход через прототип, который поймал тест) закреплены в
worker/test/routes.test.mjs и запускаются отсюда.
"""
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKER = ROOT / "worker" / "src" / "index.js"
JS_TEST = ROOT / "worker" / "test" / "routes.test.mjs"


class Engine(unittest.TestCase):
    def test_properties_hold(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node не найден")
        r = subprocess.run([node, str(JS_TEST)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=300)
        self.assertEqual(r.returncode, 0, f"провал движка маршрутов:\n{r.stdout}\n{r.stderr}")
        self.assertIn("failed 0", r.stdout)


class Boundaries(unittest.TestCase):
    def setUp(self):
        self.src = WORKER.read_text(encoding="utf-8")

    def test_a_declaration_is_interpreted_never_executed(self):
        """Ни eval, ни new Function: агент присылает спецификацию, не код."""
        seg = self.src[self.src.find("function runSpec("):self.src.find("const SPOT")]
        for forbidden in ("eval(", "new Function", "import(", "fetch("):
            self.assertNotIn(forbidden, seg, f"в интерпретаторе есть {forbidden}")

    def test_prototype_keys_cannot_slip_through(self):
        """ROUTE_BY['constructor'] у обычного объекта истинно — это и был обход."""
        self.assertIn("Object.create(null)", self.src)
        self.assertIn("hasOwnProperty.call", self.src)

    def test_a_compiled_route_cannot_be_redefined(self):
        self.assertIn("is a compiled route and cannot be redefined", self.src)

    def test_declaring_requires_the_write_token(self):
        seg = self.src[self.src.find('if (path === "/routes")'):self.src.find('if (path === "/board")')]
        self.assertIn("BOARD_TOKEN", seg)
        self.assertIn("forbidden", seg)

    def test_price_bounds_are_enforced_by_the_worker(self):
        self.assertIn("usd must be a number in", self.src)


if __name__ == "__main__":
    unittest.main()
