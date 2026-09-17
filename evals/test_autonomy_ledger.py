"""АВТОНОМНОСТЬ ДЕНЕЖНОЙ ЦЕПОЧКИ НЕ ИМЕЕТ ПРАВА ТИХО СЛОМАТЬСЯ.

Владелец спрашивал четыре раза, действительно ли агенты смогут без него всё, что
делал человек ради платежа. Ответ теперь механический — `ops/autonomy_ledger.py`.
Этот тест делает его ещё и необратимым: если звено выпадет из облака, потеряет
секрет или перестанет вызываться, набор станет красным, а красные тесты закрывают
развёртывание (self_deploy.tests_pass).

Ровно так уже ломалось молча:
  * `self_deploy` лежал в реестре улучшателя и не мог быть вызван — класс RED выше
    предела агента, постоянного разрешения не было;
  * `strategist_think` (шаг, превращающий план во внешнее действие) не работал в
    облаке вовсе — и без ноутбука владельца очередь гипотез не исполнял никто;
  * ключи правки объявлений, которые единственные приносят деньги, жили только в
    .env на машине, так что облако не могло починить цену.
"""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ops import autonomy_ledger as led  # noqa: E402


class Ledger(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = led.audit(live=False)

    def test_every_link_of_the_money_chain_is_autonomous(self):
        broken = [(r["name"], r["gaps"]) for r in self.rows if not r["autonomous"]]
        self.assertEqual(broken, [], f"звенья требуют человека: {broken}")

    def test_the_chain_still_covers_what_the_human_actually_did(self):
        """Ведомость обязана покрывать работу, которая принесла первый платёж.

        Человек руками: добавил маршруты, переставил цены, создал объявления,
        зарегистрировал в индексах, привязал ключи и развернул сервис. Если звено
        пропадёт из списка, ведомость станет зелёной от того, что перестала смотреть.
        """
        names = " | ".join(r["name"] for r in self.rows)
        for must in ("маршрут", "цен", "объявлени", "индекс", "развернуть", "платёж"):
            self.assertIn(must, names, f"ведомость перестала проверять «{must}»")
        self.assertGreaterEqual(len(self.rows), 13, "звенья пропали из ведомости")

    def test_money_steps_run_on_a_clock_not_a_queue(self):
        """Платящий канал и выкат не должны ждать очереди редких шагов."""
        on_clock = {r["name"]: r["clock"] for r in self.rows}
        for name, clock in on_clock.items():
            if any(k in name for k in ("объявлени", "развернуть", "платёж", "цен")):
                self.assertEqual(clock, "по часам", f"«{name}» снова ждёт очереди")

    def test_secrets_the_cloud_needs_are_actually_passed(self):
        """Секрет, который есть только в .env, в облаке не существует."""
        missing = [r["name"] for r in self.rows if "НЕТ (" in r["secret"]]
        self.assertEqual(missing, [], f"облаку не переданы ключи: {missing}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
