"""Проверка САМОЙ страховочной сети, а не того, что она существует.

Оба теста ниже написаны по следам двух настоящих провалов в бою:

1. deploy() отрапортовал «развёрнуто и проверено» на сервисе, где /count
   намеренно отдавал 503. Проверка успела пройти ДО того, как Cloudflare
   разнёс новую версию, то есть измерила предыдущую.
2. Когда откат наконец сработал, он сел на предыдущую версию — а предыдущей
   как раз и был тот же сломанный деплой. Один шаг назад не помог.

Поэтому здесь проверяется не «есть ли откат», а что он переживает
запаздывающий край и сломанный предшественник.
"""
import pytest

from core import roster

roster.wire()
from agents import self_deploy as sd


@pytest.fixture(autouse=True)
def no_real_waiting(monkeypatch):
    """Тесты не должны спать по-настоящему."""
    monkeypatch.setattr(sd.time, "sleep", lambda *_: None)


def test_verify_waits_before_judging(monkeypatch):
    """Проверка обязана дать версии разнестись, а не судить мгновенно."""
    waits = []
    monkeypatch.setattr(sd.time, "sleep", lambda s: waits.append(s))
    monkeypatch.setattr(sd, "sells_correctly", lambda: (True, "все отдают 402"))

    ok, _ = sd._verify_settled()
    assert ok
    assert waits, "проверка не подождала разноса — это и был баг в бою"
    assert waits[0] >= 10, f"пауза до первой проверки слишком мала: {waits[0]}с"


def test_one_late_failure_is_not_forgiven(monkeypatch):
    """Прошло-прошло-упало = НЕ годен. Единогласие, иначе край мог отвечать старым."""
    answers = [(True, "ок"), (True, "ок"), (False, "/count=503")]
    monkeypatch.setattr(sd, "sells_correctly", lambda: answers.pop(0))

    ok, detail = sd._verify_settled()
    assert not ok
    assert "/count=503" in detail


def test_broken_deploy_is_caught_on_first_check(monkeypatch):
    """Настоящая поломка видна сразу и не требует трёх кругов."""
    calls = []

    def checker():
        calls.append(1)
        return False, "/count=503"

    monkeypatch.setattr(sd, "sells_correctly", checker)
    ok, detail = sd._verify_settled()
    assert not ok
    assert len(calls) == 1, "незачем перепроверять то, что уже сломано"
    assert "проверка 1/3" in detail


def test_rollback_walks_past_a_broken_predecessor(monkeypatch):
    """Шаг назад бесполезен, если яма начинается на шаг назад."""
    healthy = "gggggggg-0000-0000-0000-000000000003"
    monkeypatch.setattr(sd, "_known_good_versions", lambda limit=5: [])
    monkeypatch.setattr(sd, "_version_history", lambda limit=8: [
        "bbbbbbbb-0000-0000-0000-000000000002", healthy])
    landed_on = []

    def fake_wrangler(args, timeout=300):
        landed_on.append(args[1])
        return 0, "ok"

    monkeypatch.setattr(sd, "_wrangler", fake_wrangler)
    monkeypatch.setattr(sd, "sells_correctly",
                        lambda: (landed_on[-1] == healthy, "продаёт" if landed_on[-1] == healthy
                                 else "/count=503"))

    ok, detail, landed = sd._rollback_to_health("aaaaaaaa-0000-0000-0000-000000000001")
    assert ok, detail
    assert landed == healthy
    assert len(landed_on) == 3, f"не дошёл до здоровой версии: {landed_on}"


def test_rollback_admits_defeat_instead_of_lying(monkeypatch):
    """Если здоровой версии нет — так и сказать, а не отрапортовать успех."""
    monkeypatch.setattr(sd, "_known_good_versions", lambda limit=5: [])
    monkeypatch.setattr(sd, "_version_history", lambda limit=8: ["x" * 36, "y" * 36])
    monkeypatch.setattr(sd, "_wrangler", lambda args, timeout=300: (0, "ok"))
    monkeypatch.setattr(sd, "sells_correctly", lambda: (False, "/count=503"))

    ok, detail, landed = sd._rollback_to_health("z" * 36)
    assert not ok
    assert landed is None
    assert "не вернул продажи" in detail


def test_failed_rollback_command_does_not_stop_the_walk(monkeypatch):
    """Отказ wrangler на одной версии — не повод бросить поиск здоровой."""
    healthy = "hhhhhhhh-0000-0000-0000-000000000009"
    monkeypatch.setattr(sd, "_known_good_versions", lambda limit=5: [])
    monkeypatch.setattr(sd, "_version_history", lambda limit=8: [healthy])
    tried = []

    def fake_wrangler(args, timeout=300):
        tried.append(args[1])
        return (1, "version not found") if len(tried) == 1 else (0, "ok")

    monkeypatch.setattr(sd, "_wrangler", fake_wrangler)
    monkeypatch.setattr(sd, "sells_correctly", lambda: (True, "продаёт"))

    ok, _, landed = sd._rollback_to_health("bad-version")
    assert ok
    assert landed == healthy


def test_the_daily_deploy_cap_actually_bites(monkeypatch):
    """CAPS['deploy_service'] обязан считаться, а не быть числом без силы.

    check_action только СЧИТАЕТ строки в таблице actions, а self_deploy туда
    ничего не писал — предел в 4 развёртывания в сутки не срабатывал никогда.
    Теперь каждая попытка развернуть пишет строку класса RED, и пятая за сутки
    упирается в предел.
    """
    from core import guard
    from core.db import connect

    c = connect()
    c.execute("CREATE TABLE IF NOT EXISTS actions (id INTEGER PRIMARY KEY, kind TEXT, "
              "action_class TEXT, dry_run INTEGER, payload TEXT, result TEXT, created_at TEXT)")
    c.execute("DELETE FROM actions WHERE kind='deploy_service'")
    c.commit(); c.close()
    cap = guard.CAPS["deploy_service"]
    for i in range(cap):
        sd._log_deploy_action(f"test {i}")
    raised = False
    try:
        guard.check_action("deploy_service", "RED")
    except guard.CapExceeded:
        raised = True
    c = connect(); c.execute("DELETE FROM actions WHERE kind='deploy_service'"); c.commit(); c.close()
    assert raised, f"развёртывание #{cap + 1} за сутки не было остановлено пределом"


def test_the_improver_can_actually_reach_self_deploy():
    """Инструмент в списке агента — ещё не право им пользоваться.

    self_deploy — класса RED, улучшатель ограничен YELLOW. Без записанного
    постоянного разрешения владельца диспетчер молча отклонял бы вызов, и вся
    самостоятельность существовала бы только на бумаге.
    """
    from core import guard
    from core.agent import TOOLS

    tool = TOOLS["self_deploy"]
    rank = {"GREEN": 0, "YELLOW": 1, "RED": 2, "BLACK": 3}
    granted = guard.standing("self_deploy")
    assert granted, "нет записанного разрешения — вызов отклонится по классу"
    assert not (tool.action_class == "BLACK"
                or (rank[tool.action_class] > rank["YELLOW"] and not granted))
    assert granted.get("пределы, которые остаются"), "разрешение без пределов не записываем"
    assert guard.CAPS.get("deploy_service"), "развёртывание без суточного предела не пускаем"


class _Done:
    def __init__(self, code, out):
        self.returncode, self.stdout, self.stderr = code, out, ""


def test_empty_collection_is_not_green(monkeypatch):
    """«Ran 0 tests» — это сломанный сбор, а не чистый прогон.

    Гейт годами звал `unittest discover`, который молча пропускал файлы в стиле
    pytest: 174 теста из 230. Тесты, которых гейт не видит, ничего не охраняют.
    """
    monkeypatch.setattr(sd.subprocess, "run",
                        lambda *a, **k: _Done(0, "no tests ran"))
    ok, detail = sd.tests_pass()
    assert not ok
    assert "0 тестов" in detail


def test_shrunken_suite_is_not_green(monkeypatch):
    """Набор внезапно вдвое меньше — значит сбор сломался, деплой стоп."""
    monkeypatch.setattr(sd.subprocess, "run",
                        lambda *a, **k: _Done(0, "100 passed in 3.2s"))
    ok, detail = sd.tests_pass()
    assert not ok
    assert str(sd.MIN_PY_TESTS) in detail


def test_worker_tests_also_gate_the_deploy(monkeypatch):
    """Платный продукт — воркер. Его красные тесты обязаны блокировать деплой."""
    seen = []

    def fake_run(cmd, **kw):
        seen.append(cmd)
        if "pytest" in cmd:
            return _Done(0, f"{sd.MIN_PY_TESTS + 5} passed in 100s")
        return _Done(1, "# fail 1\nprepaid: buyer refused")

    monkeypatch.setattr(sd.subprocess, "run", fake_run)
    ok, detail = sd.tests_pass()
    assert not ok, "красные тесты воркера пропустили деплой"
    assert "воркер" in detail
    assert any("node" in c[0] for c in seen), "тесты воркера вообще не запускались"


def test_falsely_certified_version_is_not_offered_as_known_good():
    """Версия, помеченная false_ok, НЕ должна предлагаться как здоровая.

    870e2d55 попала в историю как «ok» из-за ранней проверки, хотя отдавала 503.
    Запись исправлена на false_ok; выборка обязана её игнорировать.
    """
    assert "870e2d55-cd7e-4368-b5d0-aab23099b92e" not in sd._known_good_versions(limit=20)
