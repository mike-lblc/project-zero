"""ДЫМОВОЙ ТЕСТ ВОРКЕРА — ловит то, что однажды работало и перестало.

Почему он существует. В цикл был добавлен замер времени вокруг каждого шага, а
строка импорта не попала в файл. Воркер начал падать НА КАЖДОМ шаге с
NameError, и так проработал десятки циклов: аудит заметил это только по
косвенному признаку — по тому, что один и тот же текст ошибки повторился
тридцать один раз подряд.

Патч, который это внёс, содержал проверку `assert "telemetry" in текст`. Она
прошла — слово встречалось в только что добавленном коде. Проверка, которая не
могла упасть, хуже отсутствующей: она создаёт уверенность.

Здесь проверяется не наличие слов в исходнике, а то, что модуль ИМПОРТИРУЕТСЯ
и его шаги ВЫЗЫВАЮТСЯ. Это единственное, что нельзя подделать текстом.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class WorkerSmoke(unittest.TestCase):
    def test_worker_imports(self):
        """Модуль цикла импортируется целиком, со всеми зависимостями."""
        from agents import worker
        self.assertTrue(worker.CYCLE, "ядро цикла пусто")
        self.assertTrue(worker.SLOW_CYCLE, "редкие шаги пусты")

    def test_every_step_is_callable(self):
        """Каждый объявленный шаг — вызываемый объект, а не строка или None."""
        from agents import worker
        for name, fn in worker.CYCLE + worker.SLOW_CYCLE:
            with self.subTest(шаг=name):
                self.assertTrue(callable(fn), f"шаг «{name}» не вызываем")

    def test_names_used_by_steps_exist(self):
        """Имена, которыми пользуются шаги, определены в модуле.

        Именно этого не хватало: telemetry.span() стоял в коде шага, а самого
        имени telemetry в модуле не было. Обнаруживается только исполнением.
        """
        from agents import worker
        for attr in ("telemetry", "memory", "guard", "connect"):
            with self.subTest(имя=attr):
                self.assertTrue(hasattr(worker, attr),
                                f"модуль цикла не знает имени «{attr}»")

    def test_cheap_step_actually_runs(self):
        """Самый дешёвый шаг исполняется без исключения.

        Берём проверку кошелька: она ходит в базу и в сеть, но ничего не
        меняет, и именно она обязана работать всегда — это проверка миссии.
        """
        from agents import worker
        out = dict(worker.CYCLE)["watch_payments"]()
        self.assertIsInstance(out, str)
        self.assertTrue(out.strip(), "шаг вернул пустоту вместо ответа")

    def test_every_step_is_accounted_for(self):
        """Каждый шаг числится либо облачным, либо домашним — без забытых.

        Тринадцать шагов однажды не числились нигде, и оценка «в облаке
        работает половина» была занижена по небрежности, а не по существу.
        """
        from agents import worker
        names = [n for n, _ in worker.CYCLE] + [n for n, _ in worker.SLOW_CYCLE]
        forgotten = [n for n in names
                     if n not in worker.CLOUD_STEPS and n not in worker.CLOUD_CANNOT]
        self.assertEqual(forgotten, [], f"шаги не разобраны: {forgotten}")


if __name__ == "__main__":
    unittest.main()
