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

    def test_the_runtime_a_link_needs_is_installed_before_the_step(self):
        """Проводки мало: без бинарника звено мертво, даже будучи «настроенным».

        Так и было: deploy_if_changed стоял в облаке, с секретом и по часам, а
        `actions/setup-node` шёл ПОСЛЕ цикла и worker/node_modules в репозиторий
        не коммитится — собственные ворота деплоя падали на отсутствующем node,
        и развернуть не удалось бы ни разу.
        """
        broken = [r["name"] for r in self.rows if "НЕТ (" in r.get("runtime", "—")]
        self.assertEqual(broken, [], f"в облаке нет нужного бинарника: {broken}")


class LedgerHonesty(unittest.TestCase):
    """Ведомость, которая врёт, хуже отсутствующей.

    Разбор блока секретов искал ГОЛУЮ фразу «Непрерывный облачный цикл», а она
    встречается ещё и в шапке файла. Стоило добавить комментарий выше — и ведомость
    прочитала не тот блок и доложила «секреты облаку не переданы», хотя переданы.
    """

    def test_the_parser_anchors_on_the_step_declaration(self):
        wf = (ROOT / ".github" / "workflows" / "agents.yml").read_text(encoding="utf-8")
        at = led._loop_step_at(wf)
        self.assertGreater(at, 0, "шаг непрерывного цикла не найден")
        self.assertTrue(wf[at:].startswith(led.LOOP_STEP))
        # якорь обязан быть ОБЪЯВЛЕНИЕМ шага, а не первым упоминанием в шапке
        self.assertLess(wf.find("Непрерывный облачный цикл"), at,
                        "фраза встречается выше — значит голый поиск дал бы не тот блок")

    def test_it_finds_the_secrets_that_are_really_there(self):
        names = led._cloud_env_names()
        for must in ("CLOUDFLARE_API_TOKEN", "NOHUMANS_TOKENS", "BOARD_TOKEN"):
            self.assertIn(must, names, f"разбор не увидел переданный секрет {must}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
