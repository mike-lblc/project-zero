"""МОЛЧАНИЕ СТРАНИЦЫ — НЕ ДОКАЗАТЕЛЬСТВО, ЧТО ОНА ПЛАТИТ (17.09.2026).

Владелец: «агенты должны САМИ находить, как заработать». Разведка как раз работает
— 982 пути, 80/80 категорий. Ломалась ОЦЕНКА, и ломалась вывернутым наизнанку
правилом: базовые 10 баллов, +5 за «не упомянут аккаунт», +3 за «не упомянуты
деньги». То есть страница, НЕ СКАЗАВШАЯ НИЧЕГО, получала максимальные 18, и все
пять верхних «открытых» путей были именно такой тишиной.

Проверено живыми запросами (адверсарная проверка аудита):
  * hackerone.com стоял с needs_kyc=0 — его главная это JS-оболочка на 2690
    символов, а в собственных условиях требуется KYC/AML и проверка по списку OFAC;
  * tomtunguz.com, блог венчурного инвестора, попал как «перепродажа вычислений»
    по подстроке «reward» внутри «the saas era rewarded unbundling»;
  * slashdot.org — по «prize» внутри «millennium prize»;
  * datacoup.com — заброшенная страница 2020 года (её /dashboard отдаёт 404) — 18 баллов;
  * 10 из 18 «открытых» требовали вложений и всё равно считались открытыми.

И вторая, опаснее: дилер переоткрывал вердикт разведчика через ИЛИ, протаскивая
24 пути из 42, включая ДВА со стеной «требует подтверждения личности» — а личность
агентам запрещена классом BLACK.
"""
import inspect
import io
import pathlib
import sys
import tokenize
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.db import connect  # noqa: E402


def _code_only(src):
    out = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type in (tokenize.STRING, tokenize.COMMENT):
                continue
            out.append(tok.string)
    except tokenize.TokenError:
        return src
    return " ".join(out)


def _without_comments(src):
    """Исходник БЕЗ комментариев, но СО строками.

    Тонкость, на которой я попался трижды за день: _code_only() выбрасывает и
    комментарии, и строки — а SQL живёт именно в строке, так что проверять по нему
    SQL невозможно. Обратная ошибка так же коварна: если оставить комментарии, тест
    находит старый запрос в моём же пояснении о том, что он удалён. Нужно ровно
    одно: убрать комментарии, сохранить строки.
    """
    out = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                continue
            out.append(tok.string)
    except tokenize.TokenError:
        return src
    return "".join("".join(out).split())


class SilenceIsNotEvidence(unittest.TestCase):
    def test_no_points_are_awarded_for_absence(self):
        """Балл дают найденные признаки, а не отсутствие плохих слов."""
        from agents import prospector
        code = _code_only(inspect.getsource(prospector.probe))
        # старое правило: score += 5 сразу после `not flags [ "needs_account" ]`
        self.assertNotIn('not flags [ "needs_account" ] : score += 5', code,
                         "вернулась премия за неупоминание аккаунта")
        self.assertNotIn('not flags [ "needs_money" ] : score += 3',
                         code, "вернулась премия за неупоминание денег")

    def test_a_silent_page_cannot_reach_the_old_maximum(self):
        """Тишина обязана стоить меньше, чем содержательная страница с признаком выплаты.

        Считаем по той же формуле, что в коде, на двух искусственных случаях.
        """
        def score_for(pay_hit, text, needs_account, needs_money):
            s = 4.0
            if pay_hit:
                s += 4
            if len(text) >= 5000:
                s += 2
            if needs_account is False and "account" in text:
                s += 2
            if needs_money is False and ("fee" in text or "deposit" in text):
                s += 2
            return s

        silent = score_for(False, "x" * 2690, False, False)          # JS-оболочка, как у hackerone
        proven = score_for(True, "account " + "y" * 6000 + " fee", False, False)
        self.assertLess(silent, proven, "молчащая страница снова не хуже доказанной")
        self.assertLessEqual(silent, 6.0, f"тишина всё ещё дорогая: {silent}")


class ClosedStaysClosed(unittest.TestCase):
    """Вердикт разведчика нельзя переоткрыть, и особенно нельзя по личности."""

    OLD = ("SELECT COUNT(*) FROM money_paths WHERE payout='crypto' "
           "AND (open_to_us=1 OR needs_account=0)")
    NEW = ("SELECT COUNT(*) FROM money_paths WHERE payout='crypto' "
           "AND COALESCE(open_to_us, 1) <> 0 AND needs_kyc IS NOT 1 "
           "AND (open_to_us=1 OR (wall IS NULL AND needs_account=0))")

    def test_the_dealer_query_takes_no_walled_or_identity_path(self):
        c = connect()
        leaked = c.execute(self.NEW + " AND (open_to_us=0 OR needs_kyc=1)").fetchone()[0]
        c.close()
        self.assertEqual(leaked, 0, f"дилер снова берёт закрытые пути: {leaked}")

    def test_the_old_permissive_or_is_gone_from_the_code(self):
        """SQL живёт в строковом литерале, поэтому смотрим СЫРОЙ исходник.

        Первая версия этого теста брала _code_only(), который выбрасывает строки —
        то есть выбрасывал ровно тот SQL, который проверяет. И искала не в той
        функции: запрос лежит в dealer.weave, не в survey. Обе ошибки мои.
        """
        from agents import dealer
        raw = inspect.getsource(dealer.weave)
        flat = _without_comments(raw)
        self.assertNotIn("(open_to_us=1ORneeds_account=0)", flat,
                         "вернулось ИЛИ, отменяющее вердикт разведчика")
        self.assertIn("needs_kyc", raw, "запрос больше не исключает пути с личностью")
        self.assertIn("COALESCE(open_to_us", raw, "закрытые пути снова не исключены")

    def test_it_still_returns_something_to_work_with(self):
        """Строгость не должна означать пустоту: непроверенные пути остаются доступны."""
        c = connect()
        n = c.execute(self.NEW).fetchone()[0]
        c.close()
        self.assertGreater(n, 0, "запрос стал брать ноль путей — дилеру нечего обходить")


if __name__ == "__main__":
    unittest.main(verbosity=2)
