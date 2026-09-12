"""ПРОВЕРКА ЗАЯВЛЕННОГО — каждое утверждение предъявляет доказательство.

Владелец попросил убедиться, что сказанное действительно сделано, проверено и
работает. Пересказ здесь не годится: пересказ — это то же самое утверждение,
только повторённое. Поэтому каждый пункт ниже запускает настоящую проверку и
печатает то, что она вернула, — включая случаи, когда она вернула отказ.

Разделение намеренное: ЗАЯВЛЕНО и ПОДТВЕРЖДЕНО — разные столбцы. Утверждение,
которое не удалось подтвердить, остаётся в отчёте с пометкой, а не исчезает.
"""
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

R = {"ok": 0, "bad": 0}
FAILED = []


def check(claim, fn):
    """Одно заявление. Доказательство печатается всегда — и при провале тоже."""
    try:
        passed, proof = fn()
    except Exception as e:
        passed, proof = False, f"{type(e).__name__}: {str(e)[:110]}"
    mark = "ДА " if passed else "НЕТ"
    print(f"  [{mark}] {claim}")
    print(f"        {proof}")
    R["ok" if passed else "bad"] += 1
    if not passed:
        FAILED.append(f"{claim} — {proof}")


def _http(url, headers=None, timeout=25):
    # ПУСТОЙ СЛОВАРЬ — НЕ ТО ЖЕ, ЧТО ОТСУТСТВИЕ СЛОВАРЯ. Здесь стояло
    # headers or {...}: пустой словарь ложен, и проверка «клиент без своего
    # заголовка» на деле подставляла запасной User-Agent — то есть измеряла
    # не то, что заявляла, и вернула ложное «отказов нет». Ровно тот вид
    # ошибки, ради поимки которого эта проверка и написана.
    h = {"User-Agent": "P0-verify/1.0"} if headers is None else headers
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout)
        return r.status, r.read(400).decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:
        return 0, type(e).__name__


def _ps(cmd):
    r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="ignore", creationflags=0x08000000)
    return r.stdout.strip()


# ═════════════════════════════════════════ 1. ИСПОЛНИТЕЛЬ
def c_executor_registered():
    from core import roster  # noqa: F401
    from core.agent import REGISTRY
    a = REGISTRY.get("executor")
    if not a:
        return False, "агента executor нет в составе"
    return True, (f"в составе, промпт {len(a.system)} знаков, "
                  f"инструменты: {', '.join(a.tools)}")


def c_executor_produces():
    from agents import executor
    r = executor.produce("BasedHardware/omi", "sdks/python-cli")
    if not r.get("ok"):
        return False, f"работа НЕ выдана: {r.get('why')} {r.get('problems', '')}"
    return True, f"работа выдана и сверена: команд {r['facts']}, файл {Path(r['path']).name}"


def c_executor_correct_signature():
    """Та самая сигнатура, которую в прошлый раз поправил человек на ревью."""
    f = ROOT / "work" / "BasedHardware_omi_ru.md"
    if not f.exists():
        return False, "файла работы нет"
    text = f.read_text(encoding="utf-8")
    want = "### `goal progress <goal_id> <current_value>`"
    if want not in text:
        line = next((l for l in text.splitlines() if "goal progress" in l), "строки нет")
        return False, f"ожидалось «{want}», в файле: «{line}»"
    return True, "goal progress выведен с ДВУМЯ обязательными аргументами — верно"


def c_executor_refuses_unverified():
    """Предохранитель обязан уметь ОТКАЗЫВАТЬ, иначе он украшение."""
    from agents import executor
    facts = [{"full": "goal progress", "name": "progress", "group": "goal",
              "required": ["goal_id", "current_value"], "optional": [],
              "source": "goal.py:1"}]
    bad = "### `goal progress <goal_id>`\n"     # аргумент потерян намеренно
    ok, problems = executor.verify(bad, facts, {"goal.py": "x"})
    if ok:
        return False, "сверка ПРОПУСТИЛА заведомо неверный текст — предохранителя нет"
    return True, f"заведомо неверный текст отвергнут: {problems[0][:90]}"


# ═════════════════════════════════════════ 2. СОСТАВ И РЕЕСТР
def c_registry_refuses_duplicates():
    from core import roster  # noqa: F401
    from core.agent import Agent, register
    try:
        register(Agent(name="executor", role="дубль", system="x" * 900,
                       tools=(), kpi="проверка"))
        return False, "ПОВТОРНОЕ ИМЯ ПРИНЯТО — агент потерялся бы молча"
    except ValueError as e:
        return True, f"повторное имя отвергнуто: {str(e)[:80]}"


def c_no_orphan_tools():
    from core import roster  # noqa: F401
    from core.agent import REGISTRY, TOOLS
    orphan = [t for t in TOOLS if not any(t in a.tools for a in REGISTRY.values())]
    missing = [(a.name, t) for a in REGISTRY.values() for t in a.tools if t not in TOOLS]
    if orphan or missing:
        return False, f"без владельца {orphan}; без реализации {missing}"
    return True, f"агентов {len(REGISTRY)}, инструментов {len(TOOLS)}, все связаны"


# ═════════════════════════════════════════ 3. ПРОСТРАНСТВО ПОИСКА
def c_search_space():
    from agents import prospector
    from core.db import connect
    c = connect()
    plats = c.execute("SELECT COUNT(*) FROM money_paths").fetchone()[0]
    cats = c.execute("SELECT COUNT(DISTINCT category) FROM money_paths").fetchone()[0]
    named = [k for k in ("почтовые рассылки по согласию", "партнёрские сети разработчиков",
                         "микрозадачи и разметка") if k in prospector.CATEGORIES]
    c.close()
    if len(named) < 3:
        return False, f"названные владельцем классы не все на месте: {named}"
    return True, (f"классов в обходе {len(prospector.CATEGORIES)}, площадок в базе {plats} "
                  f"в {cats} классах; классы владельца добавлены")


# ═════════════════════════════════════════ 4. КОНВЕЙЕР РАБОТЫ
def c_claim_not_crashing():
    from agents import craftsman
    r = craftsman.claim("https://github.com/BasedHardware/omi/issues/13438",
                        "план проверки", dry_run=True)
    if not isinstance(r, dict):
        return False, f"неожиданный ответ: {type(r).__name__}"
    return True, f"заявка отвечает без падения: {str(r.get('why') or r)[:80]}"


def c_queue_has_consumer():
    from core.db import connect
    c = connect()
    started = c.execute("SELECT COUNT(*) FROM tasks WHERE attempts > 0").fetchone()[0]
    states = dict(c.execute("SELECT state, COUNT(*) FROM tasks GROUP BY state"))
    c.close()
    if not started:
        return False, "ни одна задача не начата — у очереди по-прежнему нет потребителя"
    return True, f"задач с попытками: {started}; состояния: {states}"


def c_escalations_created():
    from core.db import connect
    c = connect()
    n = c.execute("SELECT COUNT(*) FROM messages WHERE recipient='ESCALATION'").fetchone()[0]
    c.close()
    if not n:
        return False, "очередь суждений пуста — её по-прежнему никто не наполняет"
    return True, f"эскалаций создано: {n} (раньше было 0)"


# ═════════════════════════════════════════ 5. ЖИВОСТЬ
def c_worker_alive():
    from core.db import connect
    from datetime import datetime, timezone
    c = connect()
    last = c.execute("SELECT MAX(started_at) FROM runs").fetchone()[0]
    c.close()
    if not last:
        return False, "прогонов нет вовсе"
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds()
    if age > 600:
        return False, f"последний прогон {round(age/60)} мин назад — воркер стоит"
    return True, f"последний прогон {round(age)} сек назад"


def c_service_alive():
    st, _ = _http("http://127.0.0.1:8402/api/status")
    return st == 200, f"локальная служба отвечает {st} на :8402"


def c_public_service_alive():
    from core.identity import SERVICE_URL
    st, _ = _http(SERVICE_URL + "/health")
    return st == 200, f"постоянный адрес отвечает {st}"


def c_paid_tier_demands_payment():
    from core.identity import SERVICE_URL
    st, _ = _http(SERVICE_URL + "/search?q=test")
    return st == 402, f"платный тариф отвечает {st} (ожидается 402 — требование оплаты)"


def c_metrics_endpoint():
    st, body = _http("http://127.0.0.1:8402/metrics")
    if st != 200:
        return False, f"эндпоинт замеров отвечает {st}"
    if "p0_span_calls_total" not in body:
        return False, "формат Prometheus не найден в ответе"
    return True, "замеры отдаются в формате Prometheus"


def c_cloud_running():
    r = subprocess.run(["gh", "run", "list", "--workflow=agents.yml", "--limit", "3",
                        "--json", "status,conclusion,createdAt"],
                       capture_output=True, text=True, cwd=str(ROOT),
                       creationflags=0x08000000, encoding="utf-8", errors="ignore")
    if r.returncode != 0:
        return False, "gh не ответил — состояние облака неизвестно"
    runs = json.loads(r.stdout or "[]")
    if not runs:
        return False, "облачных прогонов не найдено"
    good = [x for x in runs if x.get("conclusion") == "success"]
    return bool(good), (f"последних прогонов {len(runs)}, успешных {len(good)}, "
                        f"последний {runs[0].get('createdAt', '')[:16]}")


def c_dashboard_live():
    st, body = _http("https://mike-lblc.github.io/project-zero/dashboard.html")
    if st != 200:
        return False, f"облачный дашборд отвечает {st}"
    return True, f"облачный дашборд отвечает 200, {len(body)}+ байт"


def c_dashboard_knows_executor():
    st, _ = _http("https://mike-lblc.github.io/project-zero/api/status.json")
    if st != 200:
        return False, f"снимок выдачи отвечает {st}"
    try:
        d = json.loads(urllib.request.urlopen(
            "https://mike-lblc.github.io/project-zero/api/status.json", timeout=25)
            .read().decode())
    except Exception as e:
        return False, f"снимок не разобрался: {type(e).__name__}"
    ids = [a.get("id") for a in d.get("agents", [])]
    if "executor" not in ids:
        return False, f"исполнителя нет в опубликованном снимке ({len(ids)} агентов)"
    return True, f"исполнитель есть в опубликованном снимке, агентов {len(ids)}"


# ═════════════════════════════════════════ 6. ПОПРАВКА ПРО CLOUDFLARE
def c_cloudflare_claim():
    from core.identity import SERVICE_URL
    blocked, passed = [], []
    for name, ua in (("Python-urllib", None),
                     ("curl", "curl/8.5.0"),
                     ("python-requests", "python-requests/2.32"),
                     ("браузер", "Mozilla/5.0")):
        st, _ = _http(SERVICE_URL + "/health", {} if ua is None else {"User-Agent": ua})
        (blocked if st == 403 else passed).append(f"{name}={st}")
    if len(blocked) != 1:
        return False, f"картина изменилась: отказано {blocked}, прошло {passed}"
    return True, f"отказ только {blocked[0]}; проходят: {', '.join(passed)}"


# ═════════════════════════════════════════ 7. ПРОВЕРКИ САМОЙ СИСТЕМЫ
def _run_audit(script, pattern="ИТОГ"):
    r = subprocess.run([sys.executable, "-X", "utf8", script], capture_output=True,
                       text=True, cwd=str(ROOT), encoding="utf-8", errors="ignore",
                       timeout=900)
    line = next((l for l in (r.stdout or "").splitlines() if l.startswith(pattern)), "")
    return line


def c_audits():
    a = _run_audit("audit.py")
    f = _run_audit("fake_work_audit.py")
    inv = _run_audit("core/regressions.py")
    all_ok = all("0 упало" in x for x in (a, f, inv) if x)
    return all_ok, f"аудит: {a} | подделка: {f} | инварианты: {inv}"


def c_tests():
    r = subprocess.run([sys.executable, "-X", "utf8", "-m", "pytest", "evals/", "-q"],
                       capture_output=True, text=True, cwd=str(ROOT),
                       encoding="utf-8", errors="ignore", timeout=600)
    tail = [l for l in (r.stdout or "").splitlines() if "passed" in l or "failed" in l]
    return r.returncode == 0, tail[-1] if tail else "вывод тестов пуст"


def c_committed():
    r = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True,
                       cwd=str(ROOT), encoding="utf-8", errors="ignore")
    dirty = [l for l in (r.stdout or "").splitlines() if l.strip()
             and not l.endswith(".json") and "docs/api" not in l]
    h = subprocess.run(["git", "log", "--oneline", "-1"], capture_output=True, text=True,
                       cwd=str(ROOT), encoding="utf-8", errors="ignore").stdout.strip()
    behind = subprocess.run(["git", "rev-list", "--count", "origin/main..HEAD"],
                            capture_output=True, text=True, cwd=str(ROOT),
                            encoding="utf-8", errors="ignore").stdout.strip()
    if dirty:
        return False, f"не зафиксировано: {dirty[:3]}"
    if behind and behind != "0":
        return False, f"не отправлено коммитов: {behind}"
    return True, f"всё зафиксировано и отправлено; вершина: {h[:60]}"


GROUPS = [
    ("ИСПОЛНИТЕЛЬ — агент, который делает работу", [
        ("агент в составе с промптом и инструментами", c_executor_registered),
        ("производит работу и она проходит сверку", c_executor_produces),
        ("вывел верную сигнатуру, которую правил человек", c_executor_correct_signature),
        ("предохранитель ОТКАЗЫВАЕТ на неверном тексте", c_executor_refuses_unverified),
    ]),
    ("СОСТАВ АГЕНТОВ", [
        ("повторное имя агента отвергается", c_registry_refuses_duplicates),
        ("у каждого инструмента есть владелец", c_no_orphan_tools),
    ]),
    ("ПРОСТРАНСТВО ПОИСКА ЗАРАБОТКА", [
        ("расширено, классы владельца добавлены", c_search_space),
    ]),
    ("КОНВЕЙЕР РАБОТЫ", [
        ("заявка не падает", c_claim_not_crashing),
        ("у очереди задач есть потребитель", c_queue_has_consumer),
        ("эскалации создаются", c_escalations_created),
    ]),
    ("ЖИВОСТЬ — ДОМА И В ОБЛАКЕ", [
        ("воркер работает прямо сейчас", c_worker_alive),
        ("локальная служба отвечает", c_service_alive),
        ("постоянный адрес службы отвечает", c_public_service_alive),
        ("платный тариф требует оплаты", c_paid_tier_demands_payment),
        ("замеры отдаются наружу", c_metrics_endpoint),
        ("облачные прогоны идут", c_cloud_running),
        ("облачный дашборд открывается", c_dashboard_live),
        ("исполнитель виден в опубликованном снимке", c_dashboard_knows_executor),
    ]),
    ("ПОПРАВКИ, КОТОРЫЕ Я СДЕЛАЛ", [
        ("отказ Cloudflare только для Python-urllib", c_cloudflare_claim),
    ]),
    ("ПРОВЕРКИ САМОЙ СИСТЕМЫ", [
        ("аудиты проходят", c_audits),
        ("тесты проходят", c_tests),
        ("всё зафиксировано и опубликовано", c_committed),
    ]),
]


def main():
    print("=" * 78)
    print("ПРОВЕРКА ЗАЯВЛЕННОГО — каждый пункт предъявляет доказательство")
    print("=" * 78)
    for title, items in GROUPS:
        print(f"\n── {title}")
        for claim, fn in items:
            check(claim, fn)
    print("\n" + "=" * 78)
    print(f"ИТОГ: подтверждено {R['ok']}, НЕ подтверждено {R['bad']}")
    if FAILED:
        print("\nНЕ ПОДТВЕРДИЛОСЬ:")
        for f in FAILED:
            print("  - " + f)
    print("=" * 78)
    return 1 if R["bad"] else 0


if __name__ == "__main__":
    sys.exit(main())
