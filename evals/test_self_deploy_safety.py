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


def test_deploy_if_changed_skips_when_bundle_unchanged(monkeypatch):
    """Тот же исходник — тихий no-op, а не деплой: цикл не жжёт предел на ровном месте."""
    monkeypatch.setattr(sd, "_token", lambda: "fake-token")
    monkeypatch.setattr(sd, "_bundle_hash", lambda: "abc123")
    from core.db import connect
    c = connect()
    c.execute("CREATE TABLE IF NOT EXISTS deploy_hash (id INTEGER PRIMARY KEY, hash TEXT, at TEXT)")
    c.execute("INSERT INTO deploy_hash(hash,at) VALUES ('abc123', 'now')")
    c.commit(); c.close()
    called = []
    monkeypatch.setattr(sd, "deploy", lambda reason="": called.append(reason) or "deployed")
    out = sd.deploy_if_changed()
    assert not called, "развернул, хотя бандл не менялся"
    assert "не менялся" in out


def test_deploy_if_changed_deploys_when_bundle_changed(monkeypatch):
    """Исходник изменился — полный деплой; хеш записывается только при успехе."""
    monkeypatch.setattr(sd, "_token", lambda: "fake-token")
    monkeypatch.setattr(sd, "_bundle_hash", lambda: "newhash999")
    from core.db import connect
    c = connect()
    c.execute("CREATE TABLE IF NOT EXISTS deploy_hash (id INTEGER PRIMARY KEY, hash TEXT, at TEXT)")
    c.execute("DELETE FROM deploy_hash")
    c.execute("INSERT INTO deploy_hash(hash,at) VALUES ('oldhash', 'now')")
    c.commit(); c.close()
    monkeypatch.setattr(sd, "deploy",
                        lambda reason="": "развёрнуто и проверено: 11 платных отдают 402")
    out = sd.deploy_if_changed()
    assert "развёрнуто" in out
    c = connect()
    latest = c.execute("SELECT hash FROM deploy_hash ORDER BY id DESC LIMIT 1").fetchone()[0]
    c.execute("DELETE FROM deploy_hash"); c.commit(); c.close()
    assert latest == "newhash999", "новый хеш не записан после успешного деплоя"


def test_wrangler_is_invoked_in_a_way_that_works_on_linux(monkeypatch):
    """«Работает у меня» и «работает» — разные утверждения.

    Здесь стояло shell=True со СПИСКОМ аргументов. На Windows список склеивается в
    командную строку и всё идёт; на POSIX получается /bin/sh -c "npx", где
    "wrangler" становится $0, "deploy" — $1, то есть все аргументы отбрасываются и
    запускается голый npx.

    Именно поэтому облако не развернуло воркер НИ РАЗУ, хотя шаг стоял в цикле,
    секрет был передан, node с зависимостями поставлен и суточный предел не
    исчерпан: мои деплои шли с Windows, а облачный ubuntu молча запускал npx без
    команды. Хуже того, попытка пишется в actions ДО вызова, так что облако
    сжигало бы предел 4/сутки на заведомо невозможных попытках.
    """
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        seen["shell"] = kw.get("shell")
        return _Done(0, "ok")

    monkeypatch.setattr(sd, "_token", lambda: "fake-token")
    monkeypatch.setattr(sd.subprocess, "run", fake_run)
    sd._wrangler(["deploy"])
    assert seen["cmd"] == ["npx", "wrangler", "deploy"], seen["cmd"]
    # shell разрешён только там, где npx — это .cmd и без shell его не найти
    assert seen["shell"] == (sd.os.name == "nt"), (
        f"shell={seen['shell']} на {sd.os.name}: на POSIX это отбросит аргументы")


def test_no_module_passes_a_list_with_shell_true(monkeypatch):
    """Тот же дефект не должен вернуться в другом файле.

    Он уже был в двух местах: развёртывание воркера и CLI Taskmarket.
    """
    import io
    import re
    import tokenize

    root = sd.ROOT
    offenders = []
    for path in list((root / "agents").glob("*.py")) + list((root / "core").glob("*.py")):
        raw = path.read_text(encoding="utf-8", errors="ignore")
        code = " ".join(t.string for t in tokenize.generate_tokens(io.StringIO(raw).readline)
                        if t.type not in (tokenize.STRING, tokenize.COMMENT))
        # subprocess.run([...], ..., shell=True) — список и безусловный shell
        for m in re.finditer(r"subprocess\s*\.\s*run\s*\(\s*\[", code):
            tail = code[m.end():m.end() + 400]
            if re.search(r"shell\s*=\s*True", tail):
                offenders.append(path.name)
    assert not offenders, (
        f"shell=True со списком аргументов вернулся в: {sorted(set(offenders))} — "
        f"на POSIX это запустит только первый элемент")


def test_stale_checkout_never_deploys_over_newer_code(monkeypatch):
    """Старым кодом поверх нового — никогда.

    Деплоить могут двое: машина и облако. Облако выкачивает main на СТАРТЕ и живёт
    пять часов, поэтому к середине прогона отстаёт. Замеренный случай: в сети уже
    стояли 15 платных маршрутов, а облако держало копию с 11. Разверни он свою —
    четыре маршрута исчезли бы, и откат НЕ спас бы: одиннадцать маршрутов честно
    отдают 402, значит проверка продаж прошла бы и поломки не увидела.
    """
    monkeypatch.setattr(sd, "_token", lambda: "fake-token")
    monkeypatch.setattr(sd, "_behind_origin",
                        lambda: (True, "копия abc12345 отстаёт от origin/main def67890"))
    called = []
    monkeypatch.setattr(sd, "deploy", lambda reason="": called.append(reason) or "deployed")
    monkeypatch.setattr(sd, "_log_deploy_action", lambda r: called.append("cap"))
    out = sd.deploy_if_changed()
    assert not called, "развернул устаревшую копию поверх свежей"
    assert "отстаёт" in out


def test_a_current_checkout_is_allowed_to_deploy(monkeypatch):
    """Осторожность не должна превращаться в паралич: свежая копия разворачивается."""
    monkeypatch.setattr(sd, "_token", lambda: "fake-token")
    monkeypatch.setattr(sd, "_behind_origin", lambda: (False, "совпадает с origin/main"))
    monkeypatch.setattr(sd, "_bundle_hash", lambda: "fresh-hash-1")
    from core.db import connect
    c = connect()
    c.execute("CREATE TABLE IF NOT EXISTS deploy_hash (id INTEGER PRIMARY KEY, hash TEXT, at TEXT)")
    c.execute("DELETE FROM deploy_hash")
    c.commit(); c.close()
    monkeypatch.setattr(sd, "deploy", lambda reason="": "развёрнуто и проверено: ok")
    out = sd.deploy_if_changed()
    c = connect(); c.execute("DELETE FROM deploy_hash"); c.commit(); c.close()
    assert "развёрнуто" in out


def test_unknown_git_state_does_not_forbid_deploying(monkeypatch):
    """git может быть недоступен — это не повод запретить деплой вообще."""
    def boom(*a, **k):
        raise OSError("no git")
    monkeypatch.setattr(sd.subprocess, "run", boom)
    behind, why = sd._behind_origin()
    assert behind is False
    assert "не выяснено" in why


def test_deploy_if_changed_skips_without_a_token(monkeypatch):
    """Нет ключа Cloudflare — пропуск ДО попытки, иначе облако жгло бы предел зря."""
    monkeypatch.setattr(sd, "_token", lambda: "")
    called = []
    monkeypatch.setattr(sd, "_log_deploy_action", lambda r: called.append(r))
    monkeypatch.setattr(sd, "deploy", lambda reason="": called.append("deploy") or "x")
    out = sd.deploy_if_changed()
    assert not called, "тронул предел/деплой без ключа"
    assert "нет ключа" in out.lower()


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
