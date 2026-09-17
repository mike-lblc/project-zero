"""ПОТЕРЯННЫЙ ОБОРОТ — ЭТО ТРАТА, И ОН БЫЛ ВИДЕН В ЦИФРАХ (17.09.2026).

Владелец: «the rest was waste, and the waste was actively costing us».

Две конкретные траты, замеренные, а не предположенные:

1. Модель выбирает инструмент и не передаёт обязательный аргумент. Оборот
   пропадает целиком: действие отклонено, ничего не сделано. В облаке этим
   валился ВЕСЬ прогон — дословно из журнала: closer выбрал ask_agent без
   question и to.
2. Из-за такого решения облачный прогон отмечался провалом: 3 из 20 прогонов
   красные, и все три — по этой причине, а не из-за поломки. Когда каждый
   седьмой прогон красный без причины, красный цвет перестаёт что-либо значить,
   и настоящая поломка приезжает в том же цвете.
"""
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import roster, agent as A  # noqa: E402

roster.wire()


class Base(unittest.TestCase):
    def setUp(self):
        self.saved = A.router.run
        self.calls = []

    def tearDown(self):
        A.router.run = self.saved

    def answer(self, *sequence):
        def fake(kind, prompt):
            self.calls.append(prompt)
            return sequence[min(len(self.calls) - 1, len(sequence) - 1)]
        A.router.run = fake


BAD = json.dumps({"tool": "ask_agent", "args": {}, "why": "спрошу соседа"})
GOOD = json.dumps({"tool": "ask_agent", "args": {"to": "dealer", "question": "кто платит?"},
                   "why": "уточню у дельца"})


class MissingParams(Base):
    def test_a_forgotten_argument_gets_one_correction(self):
        """Забытый аргумент — поправимая ошибка, а не потерянный оборот."""
        self.answer(BAD, GOOD)
        out = A.get("closer").decide()
        self.assertEqual(len(self.calls), 2, "поправки не было")
        self.assertTrue(out["allowed"], out["why"])
        self.assertEqual(out["args"], {"to": "dealer", "question": "кто платит?"})

    def test_the_correction_names_exactly_what_was_forgotten(self):
        """Поправка обязана называть забытое: иначе модель угадывает снова."""
        self.answer(BAD, GOOD)
        A.get("closer").decide()
        self.assertIn("question", self.calls[1])
        self.assertIn("to", self.calls[1])

    def test_a_valid_choice_costs_no_extra_call(self):
        """За правильный ответ модель не переспрашивают — это была бы новая трата."""
        self.answer(GOOD)
        out = A.get("closer").decide()
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(out["allowed"])

    def test_it_gives_up_after_one_correction(self):
        """Одна попытка, не цикл уговоров. Упрямая ошибка остаётся ошибкой."""
        self.answer(BAD, BAD, BAD)
        out = A.get("closer").decide()
        self.assertEqual(len(self.calls), 2, "модель уговаривали больше одного раза")
        self.assertFalse(out["allowed"])
        self.assertIn("отсутствуют", out["why"])


class InfraVersusJudgement(Base):
    def test_unreachable_model_is_marked_as_infrastructure(self):
        def boom(kind, prompt):
            raise ConnectionError("no model")
        A.router.run = boom
        out = A.get("closer").act()
        self.assertFalse(out["ok"])
        self.assertTrue(out.get("infra"), "недоступная модель не помечена как поломка")

    def test_a_bad_choice_is_not_infrastructure(self):
        self.answer(BAD, BAD)
        out = A.get("closer").act()
        self.assertFalse(out["ok"])
        self.assertFalse(out.get("infra"), "неудачный выбор выдан за поломку облака")

    def test_cloud_turn_fails_only_on_real_breakage(self):
        """Облачный прогон падает от поломки, а не от качества решения.

        Проверяется по КОДУ: в комментарии рядом нарочно приведена старая строка,
        чтобы было видно, что именно заменено. Первая версия этого теста падала
        как раз на своём же комментарии — ровно та ошибка, которую я утром
        исправлял в другом тесте, поэтому здесь сразу токенизация.
        """
        import io
        import tokenize
        raw = (ROOT / "ops" / "cloud_turn.py").read_text(encoding="utf-8")
        code = " ".join(t.string for t in tokenize.generate_tokens(io.StringIO(raw).readline)
                        if t.type not in (tokenize.STRING, tokenize.COMMENT))
        self.assertNotIn('else 1', code.replace(" ", " "),
                         "любое неудачное решение снова валит весь прогон")
        self.assertIn("infra", code, "поломка и решение не различаются")


if __name__ == "__main__":
    unittest.main(verbosity=2)
