"""ПРОДАЮЩУЮ ПОВЕРХНОСТЬ ДЕРЖИТ АГЕНТ, А НЕ ЧЕЛОВЕК (17.09.2026).

Замечание владельца: первый платёж получился потому, что человек руками создал
одиннадцать объявлений, переставил цены и привёл метаданные к чужой спецификации.
Агенты в это время крутили цикл и не сделали ничего из этого. Здесь закреплено
обратное: у каждого платного маршрута из тарифа появляется объявление САМО,
расхождение цены правится САМО, ключ правки сохраняется, а дубликаты не создаются.

Сеть здесь не трогается: и каталог, и база подменены.
"""
import sys
import tempfile
import inspect
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _code_only(src):
    """Исходник без строк и комментариев — чтобы запреты ловили вызовы, а не прозу."""
    import io, tokenize
    out = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type in (tokenize.STRING, tokenize.COMMENT):
                continue
            out.append(tok.string)
    except tokenize.TokenError:
        return src
    return " ".join(out)


class Base(unittest.TestCase):
    def setUp(self):
        from core import db, execution, guard
        self.db, self.ex, self.guard = db, execution, guard
        self.saved = (db.DB_PATH, set(db._SCHEMA_DONE), db._WAL_SET, execution._MIGRATED[0], guard.check_action)
        self.tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(self.tmp.name) / "s.db"
        db._SCHEMA_DONE.clear(); db._WAL_SET = False; execution._MIGRATED[0] = False
        guard.check_action = lambda *a, **k: None
        db.init().close(); execution._con().close()

        from agents import sell_surface as ss
        self.ss = ss
        self.saved_http, self.saved_cfg, self.saved_save = ss._http, ss.listings_config, ss._save_config
        self.calls = []
        self.cfg = {}
        ss.listings_config = lambda: dict(self.cfg)
        ss._save_config = lambda d: self.cfg.update(d)

    def tearDown(self):
        self.ss._http, self.ss.listings_config, self.ss._save_config = self.saved_http, self.saved_cfg, self.saved_save
        (self.db.DB_PATH, done, self.db._WAL_SET, self.ex._MIGRATED[0], self.guard.check_action) = self.saved
        self.db._SCHEMA_DONE.clear(); self.db._SCHEMA_DONE.update(done)
        import gc; gc.collect()
        self.tmp.cleanup()

    def stub(self, handler):
        def _h(url, method="GET", body=None, headers=None, timeout=60):
            self.calls.append((method, url, body, headers))
            return handler(method, url, body, headers)
        self.ss._http = _h


class Listings(Base):
    def test_a_route_without_a_listing_gets_one_created(self):
        n = [0]

        def h(method, url, body, headers):
            if method == "POST":
                n[0] += 1
                return 201, {"id": f"id-{n[0]}", "claim_token": f"tok-{n[0]}", "status": "unverified"}
            return 200, {}
        self.stub(h)
        out = self.ss.ensure_listings(limit=2)
        self.assertIn("создано", out)
        self.assertEqual(n[0], 2, "предел создания за ход не соблюдён")
        # ключ правки должен лечь в базу: без него агент не сможет починить цену
        c = self.db.connect()
        toks = c.execute("SELECT listing_id, claim_token FROM directory_claim").fetchall()
        c.close()
        self.assertEqual(len(toks), 2)
        self.assertTrue(all(t[1] for t in toks))

    def test_existing_listings_are_not_duplicated(self):
        from core.identity import TARIFF
        self.cfg = {p.lstrip("/"): f"have-{p}" for p in TARIFF}
        self.stub(lambda m, u, b, h: (500, {"_err": "should not be called"}))
        out = self.ss.ensure_listings()
        self.assertIn("на месте", out)
        self.assertEqual([c for c in self.calls if c[0] == "POST"], [], "созданы дубликаты")

    def test_a_listing_without_a_route_is_named_not_deleted(self):
        from core.identity import TARIFF
        self.cfg = {p.lstrip("/"): f"have-{p}" for p in TARIFF}
        self.cfg["ghost"] = "id-ghost"
        self.stub(lambda m, u, b, h: (200, {}))
        out = self.ss.ensure_listings()
        self.assertIn("ghost", out, "лишнее объявление должно быть названо")
        self.assertEqual([c for c in self.calls if c[0] == "DELETE"], [], "агент не удаляет объявления сам")


class Prices(Base):
    def test_drifted_price_is_patched_with_the_stored_token(self):
        self.cfg = {"networks": "id-net"}
        c = self.db.connect(); self.ss._table(c)
        c.execute("INSERT INTO directory_claim(listing_id,route,claim_token,created_at) VALUES (?,?,?,?)",
                  ("id-net", "networks", "tok-net", "now"))
        c.commit(); c.close()
        patched = {}

        def h(method, url, body, headers):
            if method == "GET":
                return 200, {"price_amount": 0.05}          # каталог думает, что цена другая
            if method == "PATCH":
                patched["body"] = body; patched["hdr"] = headers
                return 200, {}
            return 200, {}
        self.stub(h)
        out = self.ss.fix_price_drift()
        self.assertIn("подравнено", out)
        from core.identity import TARIFF
        self.assertEqual(patched["body"]["price_amount"], TARIFF["/networks"]["usd"])
        self.assertEqual(patched["hdr"]["x-claim-token"], "tok-net")

    def test_without_a_token_the_drift_is_reported_not_silently_ignored(self):
        """Настоящее отсутствие ключа: ни в базе, ни в окружении.

        Окружение здесь отключается НАРОЧНО. У маршрута /networks ключ правки
        реально лежит в .env владельца, и без изоляции этот тест проверял бы
        наличие ключа на машине, а не поведение «ключа нет». Проверяем то, что
        заявлено: нет ключа — расхождение НАЗЫВАЕТСЯ, а не глотается.
        """
        self.cfg = {"networks": "id-net"}
        import agents.worker as _w
        saved = _w._env_value
        _w._env_value = lambda name: ""
        try:
            self.stub(lambda m, u, b, h: (200, {"price_amount": 0.05}) if m == "GET" else (200, {}))
            out = self.ss.fix_price_drift()
        finally:
            _w._env_value = saved
        self.assertIn("ключа правки нет", out)

    def test_a_hand_made_listing_is_repairable_from_the_environment(self):
        """Ключи ЗАРАБАТЫВАЮЩИХ объявлений лежат в окружении, а не в базе.

        Одиннадцать объявлений, которые единственные приносят деньги, создавались
        руками, и их ключи легли в .env (NOHUMANS_TOKEN_<МАРШРУТ>), тогда как
        таблица directory_claim осталась пустой. Пока правка искала ключ только в
        базе, расхождение цены в платящем канале починить было НЕЧЕМ — а каталог
        за price_drift валит маршрут.
        """
        self.cfg = {"networks": "id-net"}
        patched = {}

        def h(method, url, body, headers):
            if method == "GET":
                return 200, {"price_amount": 0.05}
            if method == "PATCH":
                patched["hdr"] = headers
                return 200, {}
            return 200, {}

        import agents.worker as _w
        saved = _w._env_value
        _w._env_value = lambda name: "env-tok" if name == "NOHUMANS_TOKEN_NETWORKS" else ""
        try:
            self.stub(h)
            out = self.ss.fix_price_drift()
        finally:
            _w._env_value = saved
        self.assertIn("подравнено", out)
        self.assertEqual(patched["hdr"]["x-claim-token"], "env-tok")



class Repricing(Base):
    """Переоценка БЕЗ развёртывания — то самое действие, которое 17.09 сделал человек."""

    def _live(self, effective):
        def h(method, url, body, headers):
            if url.endswith("/prices") and method == "GET":
                return 200, {"compiled": effective, "effective": effective,
                             "override": {}, "floor": 0.0005, "cap": 1.0}
            if url.endswith("/prices") and method == "POST":
                self.posted = (body, headers)
                return 200, {"ok": True, "override": body}
            return 200, {}
        self.posted = None
        self.stub(h)

    def test_primitives_go_to_p10_and_analysis_to_median(self):
        self.ss.market_percentiles = lambda: {"n": 15000, "p10": 0.001, "median": 0.01, "p90": 0.1}
        self._live({"/count": 0.05, "/report": 0.0005})
        self.ss._board_token = lambda: "tok"
        out = self.ss.reprice()
        body = self.posted[0]
        self.assertIn("/count", body, out)          # примитив дороже p10 -> вниз
        self.assertIn("/report", body, out)         # аналитика дешевле медианы -> вверх

    def test_a_full_export_is_not_priced_like_a_single_query(self):
        """Первый прогон предложил срезать /dataset в 25 раз по медиане рынка."""
        self.ss.market_percentiles = lambda: {"n": 15000, "p10": 0.001, "median": 0.01, "p90": 0.1}
        self._live({"/dataset": 0.25})
        self.ss._board_token = lambda: "tok"
        self.ss.reprice()
        self.assertAlmostEqual(self.posted[0]["/dataset"], 0.125, places=6)

    def test_no_price_moves_more_than_twofold_in_one_run(self):
        self.ss.market_percentiles = lambda: {"n": 15000, "p10": 0.001, "median": 0.01, "p90": 0.1}
        self._live({"/count": 1.0})
        self.ss._board_token = lambda: "tok"
        self.ss.reprice()
        self.assertAlmostEqual(self.posted[0]["/count"], 0.5, places=6)

    def test_prices_stay_inside_the_worker_bounds(self):
        self.ss.market_percentiles = lambda: {"n": 15000, "p10": 0.0000001, "median": 99.0, "p90": 99.0}
        self._live({"/count": 0.001, "/report": 0.02})
        self.ss._board_token = lambda: "tok"
        self.ss.reprice()
        for v in (self.posted[0] or {}).values():
            self.assertGreaterEqual(v, self.ss.PRICE_FLOOR)
            self.assertLessEqual(v, self.ss.PRICE_CAP)

    def test_without_a_token_it_proposes_rather_than_failing_silently(self):
        self.ss.market_percentiles = lambda: {"n": 15000, "p10": 0.001, "median": 0.01, "p90": 0.1}
        self._live({"/dataset": 0.25})
        self.ss._board_token = lambda: ""
        out = self.ss.reprice()
        self.assertIn("BOARD_TOKEN", out)
        self.assertIsNone(self.posted)

    def test_a_market_it_cannot_measure_leaves_prices_alone(self):
        self.ss.market_percentiles = lambda: None
        self._live({"/count": 0.05})
        out = self.ss.reprice()
        self.assertIn("не трогаем", out)

class Boundaries(unittest.TestCase):
    def test_the_agent_never_edits_code_or_deploys(self):
        """Агент, который сам себе правит платный сервис и деплоит его, — риск без надзора.

        Запрет проверяется по КОДУ, а не по всему файлу: документация обязана иметь
        право сказать, что разворачивает отдельный агент, и не ронять этим тест.
        Строки и комментарии выброшены, вызовы остались.
        """
        raw = (ROOT / "agents" / "sell_surface.py").read_text(encoding="utf-8")
        src = _code_only(raw)
        for forbidden in ("wrangler", "subprocess", "deploy", "write_text", "os.system"):
            if forbidden == "write_text":
                # РАЗРЕШЕНЫ РОВНО ДВА ФАЙЛА, И ОБА — ДАННЫЕ, НЕ КОД:
                #   CONFIG — список публичных идентификаторов объявлений;
                #   BAKED  — worker/routes.json, маршруты, объявленные ДАННЫМИ.
                # Второй появился потому, что бесплатный предел KV исчерпывается, а
                # платный маршрут терять нельзя: число маршрутов определяет, сколько
                # оплаченных проверок пришлёт скаут каталога. Запрет на правку КОДА
                # от этого не ослабевает — он проверяется ниже по именам файлов.
                # Третий файл — CURSOR: одно число, докуда дошло окно проверки
                # витрины. Появился, когда выяснилось, что шаг осматривал только
                # первые 11 маршрутов из 62. Это данные, как и первые два; запрет
                # на правку КОДА проверяется ниже по именам и расширениям.
                self.assertLessEqual(src.count("write_text"), 3,
                                     "агент пишет файлы шире, чем объявления, маршруты и курсор")
                continue
            self.assertNotIn(forbidden, src, f"в шаге есть {forbidden}")

    def test_the_agent_writes_only_data_files_never_source(self):
        """Что именно разрешено писать: два файла данных и ничего с кодом."""
        from agents import sell_surface
        targets = {sell_surface.CONFIG.name, sell_surface.BAKED.name, sell_surface.CURSOR.name}
        self.assertEqual(targets, {"nohumans_listings.json", "routes.json", "surface_cursor.txt"})
        for t in targets:
            self.assertTrue(t.endswith((".json", ".txt")), f"агент пишет не-данные: {t}")
        # Каждый обязан лежать в каталоге данных (data/) или рядом с воркером
        # (worker/routes.json — маршруты, объявленные ДАННЫМИ), но не в agents/ и
        # не в core/, где живёт код.
        for f in (sell_surface.CONFIG, sell_surface.BAKED, sell_surface.CURSOR):
            self.assertIn(f.parent.name, {"data", "worker"}, f"файл вне каталогов данных: {f}")
        raw = (ROOT / "agents" / "sell_surface.py").read_text(encoding="utf-8")
        src = _code_only(raw)
        for source_ext in (".py", ".js", ".mjs", ".sh", ".yml"):
            self.assertNotIn(f'"{source_ext}"', src, f"в шаге появилась запись в {source_ext}")

    def test_it_is_owned_by_an_agent_and_runs_in_the_cycle(self):
        from agents import worker
        from core import roster
        self.assertIn("sell_surface", dict(worker.CYCLE + worker.SLOW_CYCLE))
        self.assertEqual(worker.AGENT_OF.get("sell_surface"), "dealer")
        self.assertIn("sell_surface", roster.wire()["dealer"].tools)
        self.assertIn("sell_surface", worker.CLOUD_STEPS)


if __name__ == "__main__":
    unittest.main()


class BuyerCount(unittest.TestCase):
    """Счёт РАЗНЫХ покупателей — по книге поступлений, а не на глаз.

    Раньше это считал наблюдатель на bash, и считал неверно: переменная уходила
    питону аргументом вместо переменной среды, `os.environ` был пуст, и давно
    известный плательщик каждые пять минут объявлялся новым. Здесь проверяется то,
    из-за чего тот счётчик врал.
    """

    def test_repeat_transfers_are_one_buyer(self):
        """Двенадцать переводов скаута — один покупатель, а не двенадцать."""
        from agents import sell_surface
        line = sell_surface.buyers()
        n = int(re.search(r"разных покупателей (\d+)", line).group(1))
        total = int(re.search(r"поступлений (\d+)", line).group(1))
        if not total:
            self.skipTest("книга поступлений пуста — считать нечего")
        # Повторные платежи одного адреса не плодят покупателей: у скаута каталога
        # двенадцать переводов и один адрес.
        self.assertLessEqual(n, total)
        self.assertGreaterEqual(n, 1)
        if total > 1:
            self.assertLess(n, total, "число покупателей сравнялось с числом переводов")

    def test_target_is_stated_so_progress_is_checkable(self):
        from agents import sell_surface
        line = sell_surface.buyers()
        self.assertIn(f"из {sell_surface.BUYER_TARGET}", line)

    def test_known_buyer_is_announced_only_once(self):
        """Второй вызов подряд не объявляет того же покупателя новым."""
        from agents import sell_surface
        sell_surface.buyers()
        again = sell_surface.buyers()
        self.assertNotIn("НОВЫЙ ПОКУПАТЕЛЬ", again)

    def test_buyers_runs_inside_the_cycle(self):
        """Счёт обязан идти сам, без человека, который его позовёт."""
        from agents import sell_surface
        src = inspect.getsource(sell_surface.cycle)
        self.assertIn("buyers()", src)


class AdoptCloudCreatedListings(Base):
    """«Уже объявлено» — не отказ, а возвращённый идентификатор.

    Расхождение, вскрытое 18.09 и дороже, чем выглядит: облачный прогон создаёт
    объявления сам, но его копия файла со списком домой не приезжает — облачная и
    локальная базы разные. Из 13 попыток ОДИННАДЦАТЬ вернули 409 с готовым
    listing_id: объявления в каталоге есть, а мы про них не знаем.

    Без идентификатора `fix_price_drift` не может подравнять цену, а расхождение
    цены каталог считает price_drift и ВАЛИТ маршрут. То есть мы теряли бы платящие
    объявления и даже не видели, какие.
    """

    def test_a_409_with_an_id_is_adopted_not_discarded(self):
        self.cfg = {}
        seen = {}

        def h(method, url, body, headers):
            if method == "POST":
                return 409, {"error": "endpoint_already_listed", "listing_id": "cloud-made-42"}
            return 200, {}

        self.stub(h)
        out = self.ss.ensure_listings(limit=1)
        self.assertIn("под наблюдение", out, f"идентификатор из 409 выброшен: {out[:120]}")
        self.assertIn("cloud-made-42", self.ss.listings_config().values().__str__(),
                      "объявление, созданное облаком, не записано в список")

    def test_adoption_is_honest_about_the_missing_edit_key(self):
        """У принятого объявления нет ключа правки — это обязано быть сказано."""
        self.cfg = {}
        self.stub(lambda m, u, b, h: (409, {"error": "endpoint_already_listed",
                                            "listing_id": "x1"}) if m == "POST" else (200, {}))
        out = self.ss.ensure_listings(limit=1)
        self.assertIn("ключа правки нет", out,
                      "молчим о том, что цену такому объявлению не подравнять")

    def test_a_409_without_an_id_is_still_a_failure(self):
        """Нечего принимать — значит отказ, а не тихое «всё хорошо»."""
        self.cfg = {}
        self.stub(lambda m, u, b, h: (409, {"error": "endpoint_already_listed"})
                  if m == "POST" else (200, {}))
        out = self.ss.ensure_listings(limit=1)
        self.assertIn("не создано", out)
