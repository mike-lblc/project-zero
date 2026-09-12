"""ПОИСК И РЕЕСТР СПОСОБОВ ЗАРАБОТКА — то, из-за чего обход рынка стоял сутками.

Что здесь закреплено и почему.

1. Поиск возвращал пустой список, когда на деле был сломан: Brave отвечал 429,
   DuckDuckGo рвал TLS, а из разметки Bing шаблон не извлекал ни одной ссылки.
   Проспектор принимал пустоту за «в этом классе денег нет».
2. Bing отдавал на запрос про рынки предсказаний страницы про актрису. Починка
   одного разбора превратила бы это в «найденные площадки».
3. Класс-сирота без запросов стоял первым в очереди и занимал место при каждом
   заходе: «разведаны классы: продажа данных, предсказания и рынки» — раз за разом.
4. Директива перечисляет пятьдесят способов заработка; каждый обязан указывать
   на существующий класс обхода.

Сеть здесь не нужна: ответы поисковиков подставляются. Проверяется логика
различения исходов, а не доступность чужих серверов.
"""
import json
import sys
import unittest
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agents import scout, prospector  # noqa: E402

DDG_RESULT = ('<a rel="nofollow" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org'
              '%2Fprediction-market-api" class=\'result-link\'>Prediction market API guide</a>')
DDG_CAPTCHA = ("<html><body>Unfortunately, bots use DuckDuckGo too. Please complete the "
               "following challenge to confirm this search was made by a human.</body></html>")
DDG_OFFTOPIC = ('<a href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fimdb.com%2Fname%2Fnm0000678" '
                'class=\'result-link\'>Kathleen Turner - IMDb</a>')
BING_LIKE = '<h2 class=""><a href="https://www.bing.com/ck/a?u=a1xyz">Anything</a></h2>' * 10


class FakeEngines:
    """Подменяет scout._get: каждому движку — свой заранее заданный ответ."""

    def __init__(self, **answers):
        self.answers = answers

    def __call__(self, url, timeout=30):
        for name, host in (("ddg", "duckduckgo"), ("marginalia", "marginalia"),
                           ("brave", "brave")):
            if host in url:
                a = self.answers.get(name, OSError("нет ответа"))
                if isinstance(a, Exception):
                    raise a
                return a
        raise OSError("неизвестный движок")


class SearchOutcomes(unittest.TestCase):
    def setUp(self):
        self._get = scout._get
        self._gap = scout.MIN_GAP_SEC
        scout.MIN_GAP_SEC = 0
        scout._COOLDOWN.clear()
        scout._LAST.clear()
        self._guard = scout.guard.check_action
        scout.guard.check_action = lambda *a, **k: None

    def tearDown(self):
        scout._get = self._get
        scout.MIN_GAP_SEC = self._gap
        scout._COOLDOWN.clear()
        scout._LAST.clear()
        scout.guard.check_action = self._guard

    def run_search(self, **answers):
        scout._get = FakeEngines(**answers)
        return scout.search("prediction market api rewards accuracy", limit=5)

    def test_relevant_results_are_returned_unwrapped(self):
        out = self.run_search(ddg=DDG_RESULT)
        self.assertEqual(out[0]["url"], "https://example.org/prediction-market-api")
        self.assertEqual(out[0]["engine"], "ddg-lite")

    def test_all_engines_broken_is_error_not_empty(self):
        """Главное: поломка всех движков — ошибка, а не «ничего не найдено»."""
        out = self.run_search(ddg=BING_LIKE,
                              marginalia=OSError("timeout"),
                              brave=urllib.error.HTTPError("u", 429, "Too Many", {}, None))
        self.assertTrue(out and "error" in out[0], f"поломка выдана за пустоту: {out}")

    def test_captcha_is_error_and_engine_pauses(self):
        out = self.run_search(ddg=DDG_CAPTCHA)
        self.assertIn("error", out[0])
        self.assertIn("ddg-lite", scout._COOLDOWN, "после проверки «человек ли вы» нет паузы")

    def test_offtopic_results_are_rejected(self):
        """Подменённая выдача не превращается в найденные площадки."""
        out = self.run_search(ddg=DDG_OFFTOPIC)
        self.assertTrue(out and "error" in out[0], f"чужая выдача принята: {out}")

    def test_honest_empty_from_api_is_empty(self):
        out = self.run_search(ddg=OSError("tls"),
                              marginalia=json.dumps({"results": []}))
        self.assertEqual(out, [])

    def test_429_sets_cooldown(self):
        self.run_search(ddg=OSError("tls"), marginalia=OSError("t"),
                        brave=urllib.error.HTTPError("u", 429, "Too Many", {}, None))
        self.assertIn("brave", scout._COOLDOWN)


class Registry(unittest.TestCase):
    def test_all_fifty_methods_map_to_existing_classes(self):
        self.assertEqual(sorted(prospector.GND_METHODS), list(range(1, 51)))
        missing = {n: c for n, c in prospector.GND_METHODS.items()
                   if c is not None and c not in prospector.CATEGORIES}
        self.assertFalse(missing, f"способы без класса обхода: {missing}")

    def test_no_query_is_duplicated_across_classes(self):
        seen = {}
        for cat, queries in prospector.CATEGORIES.items():
            for q in queries:
                self.assertNotIn(q, seen, f"запрос «{q}» у «{cat}» и «{seen.get(q)}»")
                seen[q] = cat

    def test_every_class_has_queries(self):
        empty = [c for c, q in prospector.CATEGORIES.items() if not q]
        self.assertFalse(empty, f"классы без запросов: {empty}")

    def test_no_literal_is_declared_twice(self):
        """Словарь молча затирает повторный ключ — ловим это по исходнику."""
        import re
        from collections import Counter
        src = (ROOT / "agents" / "prospector.py").read_text(encoding="utf-8")
        block = src[src.index("CATEGORIES = {"):src.index("\n}", src.index("CATEGORIES = {"))]
        keys = re.findall(r'^\s+"([^"]+)":\s*\[', block, re.M)
        dup = [k for k, n in Counter(keys).items() if n > 1]
        self.assertFalse(dup, f"ключ объявлен дважды: {dup}")
        self.assertEqual(len(keys), len(prospector.CATEGORIES))


class Queue(unittest.TestCase):
    def test_orphan_does_not_take_a_slot(self):
        space = {"гранты": ["q1"], "баунти за код": ["q2"], "аудит api": ["q3"]}
        ordered = ["продажа данных", "гранты", "баунти за код", "аудит api"]
        due, orphans = prospector.pick_due(ordered, space, 2)
        self.assertEqual(due, ["гранты", "баунти за код"])
        self.assertEqual(orphans, ["продажа данных"])


if __name__ == "__main__":
    unittest.main()
