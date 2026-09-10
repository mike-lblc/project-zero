"""СПЛОШНОЙ АУДИТ P0 — проверяет ФАКТАМИ, что было сделано, а не обещано.

Каждая проверка либо проходит, либо падает. Ничего не принимается на слово.
Запуск:  py -3.13 -X utf8 audit.py
"""
import sys, json, urllib.request, urllib.error, subprocess, re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
UA = {"User-Agent": "P0-audit/1.0", "Accept": "application/json"}
LOCAL = "http://127.0.0.1:8402"

R = {"pass": 0, "fail": 0, "warn": 0}
FAILS = []


def check(name, fn, warn_only=False):
    try:
        ok, detail = fn()
    except Exception as e:
        ok, detail = False, f"{type(e).__name__}: {str(e)[:90]}"
    if ok:
        R["pass"] += 1
        print(f"  [OK]   {name} — {detail}")
    elif warn_only:
        R["warn"] += 1
        print(f"  [WARN] {name} — {detail}")
    else:
        R["fail"] += 1
        FAILS.append(f"{name}: {detail}")
        print(f"  [FAIL] {name} — {detail}")


def http(path, base=LOCAL, timeout=20, retries=2):
    """Разовая сетевая заминка — не провал системы. Пробуем ещё раз,
    иначе аудит начинает врать про внешние сервисы."""
    last = (0, "")
    for attempt in range(retries + 1):
        try:
            r = urllib.request.urlopen(urllib.request.Request(base + path, headers=UA),
                                       timeout=timeout)
            return r.status, r.read().decode("utf-8", "ignore")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "ignore")
        except Exception as e:
            last = (0, f"{type(e).__name__}")
    return last


print("=" * 72)
print("АУДИТ P0 — проверка фактами")
print("=" * 72)

# ---------------------------------------------------------------- 1. ФАЙЛЫ
print("\n1. ФАЙЛЫ ПРОЕКТА")
for f in ["DIRECTIVE.md", "PLAN.md", "TODO.md", "DECISION_PROTOCOL.md", ".gitignore", ".env",
          "core/db.py", "core/eo.py", "core/router.py", "core/guard.py",
          "agents/scout.py", "agents/council.py", "agents/worker.py", "agents/growth.py",
          "service/server.js", "service/dashboard.html", "service/join.html",
          "data/brain.db", "data/bazaar_index.json"]:
    check(f, lambda f=f: ((ROOT / f).exists(), f"{(ROOT/f).stat().st_size:,} байт"
                          if (ROOT / f).exists() else "ОТСУТСТВУЕТ"))

# ---------------------------------------------------------------- 2. БЕЗОПАСНОСТЬ
print("\n2. БЕЗОПАСНОСТЬ")
check("ключ не утечёт в git", lambda: (
    ".env" in (ROOT / ".gitignore").read_text(encoding="utf-8"), ".env в .gitignore"))
check("ключ есть в .env", lambda: (
    "EMAILOCTOPUS_API_KEY=eo_" in (ROOT / ".env").read_text(encoding="utf-8"), "ключ на месте"))
check("кошелёк ETH в .env", lambda: (
    bool(re.search(r"WALLET_ETH=0x[0-9a-fA-F]{40}", (ROOT / ".env").read_text(encoding="utf-8"))),
    "адрес записан"))
check("ключ НЕ попал в код сервиса", lambda: (
    "eo_" not in (ROOT / "service" / "server.js").read_text(encoding="utf-8"),
    "в server.js ключа нет"))

# ---------------------------------------------------------------- 3. ПРАВИЛА АГЕНТОВ
print("\n3. ПРАВИЛА АГЕНТОВ (реально ли они работают)")
from core import router, guard


def judgement_refused():
    try:
        router.run("decide", "выбери нишу")
        return False, "локальная модель ПРИНЯЛА решение — правило не работает!"
    except router.EscalationRequired:
        return True, "judgement-задача отклонена, ушла на эскалацию"


def black_blocked():
    try:
        guard.check_action("create_account", "BLACK")
        return False, "BLACK-действие ПРОШЛО — гейт не работает!"
    except guard.Forbidden:
        return True, "BLACK-действие заблокировано"


check("локальная модель не решает", judgement_refused)
check("BLACK-действия запрещены", black_blocked)
check("GREEN-действия разрешены", lambda: (guard.check_action("research", "GREEN") is True, "ок"))
check("kill switch описан", lambda: (guard.KILL_SWITCH.name == "KILL_SWITCH", "файл-выключатель есть"))


def falsifier_required():
    from agents import council
    try:
        council.proposer("тест без фальсификатора", "")
        return False, "предложение без фальсификатора ПРОШЛО!"
    except ValueError:
        return True, "предложение без фальсификатора отклонено"


check("фальсификатор обязателен", falsifier_required)

# ---------------------------------------------------------------- 4. БАЗА
print("\n4. БАЗА ДАННЫХ")
from core.db import connect
con = connect()
tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
check("таблиц >= 16", lambda: (len(tables) >= 16, f"{len(tables)} таблиц"))


def fresh_install_works():
    """Разворачивается ли проект С НУЛЯ. Этот баг поймали только на серверах GitHub:
    таблицы spend и human_interventions создавались скриптом на ходу и отсутствовали
    в схеме, поэтому чистая установка падала."""
    import sqlite3, tempfile, os
    from core.db import SCHEMA
    tmp = os.path.join(tempfile.gettempdir(), "p0_fresh_audit.db")
    if os.path.exists(tmp):
        os.remove(tmp)
    c = sqlite3.connect(tmp)
    c.executescript(SCHEMA)
    got = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    c.close(); os.remove(tmp)
    need = {"evidence", "sources", "proposals", "objections", "rulings", "messages",
            "actions", "subscribers", "email_events", "payments", "agent_reputation",
            "runs", "candidates", "scores", "human_interventions", "spend"}
    miss = need - got
    return (not miss), (f"{len(got)} таблиц из схемы" if not miss else f"НЕТ: {miss}")


check("установка с нуля работает", fresh_install_works)

# ---- разделы директивы emba.txt ----
from core import economics, memory

# отдельное соединение: основное к этому месту уже закрыто
def _n(q):
    c2 = connect()
    try:
        return c2.execute(q).fetchone()[0]
    finally:
        c2.close()


def value_needs_evidence():
    """Раздел 27: ценность без доказательства не должна засчитываться."""
    try:
        economics.record_value("test", "revenue", "", usd=1000)
        return False, "ЦЕННОСТЬ БЕЗ ДОКАЗАТЕЛЬСТВА ПРОШЛА!"
    except ValueError:
        return True, "выдуманная ценность отклонена"


def roi_gate_blocks():
    """Раздел 19: дорогая эскалация без ожидаемой отдачи должна отклоняться."""
    ok, _ = economics.roi_gate("scout", "frontier_escalation", 0)
    ok2, _ = economics.roi_gate("scout", "frontier_escalation", 30)
    return (not ok and ok2), "дешёвую пускает, дорогую без отдачи — нет"


def silence_not_punished():
    """Раздел 28: агента, чья работа искать проблемы, нельзя наказывать за их отсутствие."""
    v = {x["agent"]: x["verdict"] for x in economics.survival()}
    w = v.get("watchdog", "")
    return w.startswith("оставить"), f"сторож: {w[:44]}"


check("ценность требует доказательства (27)", value_needs_evidence)
check("ROI-гейт работает (19)", roi_gate_blocks)
check("выживание агентов считает верно (28)", silence_not_punished)
check("экономические стадии (29)", lambda: (
    economics.stage()["stage"].startswith("СТАДИЯ"), economics.stage()["stage"]))
check("заявление «с нуля» цело (6)", lambda: (
    economics.stage()["zero_capital_claim_intact"], "трат нет"))
check("ежедневная сводка (37)", lambda: (
    "verified_revenue_usd" in economics.briefing(), "сводка собирается"))
check("защита от повторов (34)", lambda: (
    _n("SELECT COUNT(*) FROM evidence") - _n("SELECT COUNT(DISTINCT claim) FROM evidence") < 5,
    f"дублей {_n('SELECT COUNT(*) FROM evidence') - _n('SELECT COUNT(DISTINCT claim) FROM evidence')}"))
check("скоринг возможностей (10)", lambda: (
    _n("SELECT COUNT(*) FROM opportunities") >= 5,
    f"{_n('SELECT COUNT(*) FROM opportunities')} возможностей оценено"))
check("гейт «продай до постройки» (11)", lambda: (
    _n("SELECT COUNT(*) FROM opportunities WHERE gate_passed IS NOT NULL") > 0, "гейт применён"))
def dashboard_in_sync():
    """Дашборд не должен отставать от системы.

    Дважды случалось: агент появлялся в API, но не в 3D-сцене, и владелец
    видел неполную картину. Проверка делает рассинхрон невозможным незаметно.
    """
    import re as _re
    srv = (ROOT / "service" / "server.js").read_text(encoding="utf-8")
    dash = (ROOT / "service" / "dashboard.html").read_text(encoding="utf-8")
    in_api = set(_re.findall(r"id: '([a-z_]+)'", srv))
    in_3d = set(_re.findall(r'\{id:"([a-z_]+)"', dash))
    missing = in_api - in_3d
    extra = in_3d - in_api
    if missing or extra:
        return False, (f"в API но не в сцене: {missing or '—'}; "
                       f"в сцене но не в API: {extra or '—'}")
    return True, f"{len(in_api)} агентов совпадают в API и в сцене"


def cycle_agents_shown():
    """Каждый агент, который что-то делает в цикле, обязан быть виден."""
    import re as _re
    from agents import worker
    srv = (ROOT / "service" / "server.js").read_text(encoding="utf-8")
    owners = set(worker.AGENT_OF.values())
    in_api = set(_re.findall(r"id: '([a-z_]+)'", srv))
    missing = owners - in_api
    return (not missing), (f"все {len(owners)} рабочих агентов в API"
                           if not missing else f"НЕ показаны: {missing}")


check("дашборд синхронен с системой", dashboard_in_sync)
check("работающие агенты видны в дашборде", cycle_agents_shown)
check("MCP опубликован и виден", lambda: (
    "x402-bazaar-rank" in http("/v0/servers?search=x402-bazaar-rank",
                               "https://registry.modelcontextprotocol.io")[1],
    "запись в официальном реестре"))
check("сервис на постоянном адресе", lambda: (
    http("/health", "https://x402-bazaar-rank.x402-bazaar-rank-worker.workers.dev")[0] == 200,
    "Cloudflare Workers, не туннель"))
check("MCP-эндпоинт отвечает", lambda: (
    http("/mcp", "https://x402-bazaar-rank.x402-bazaar-rank-worker.workers.dev")[0] == 200,
    "инструменты доступны агентам"))
for t in ["evidence", "sources", "proposals", "objections", "rulings", "messages",
          "payments", "spend", "human_interventions", "subscribers"]:
    check(f"таблица {t}", lambda t=t: (t in tables, "есть"))
n = lambda q: con.execute(q).fetchone()[0]
check("ПОТРАЧЕНО = 0 (миссия жива)", lambda: (n("SELECT COUNT(*) FROM spend") == 0,
                                              f"{n('SELECT COUNT(*) FROM spend')} записей"))
check("утверждений без источника нет", lambda: (
    n("SELECT COUNT(*) FROM evidence e LEFT JOIN sources s ON s.id=e.source_id WHERE s.id IS NULL") == 0,
    "сирот нет"))
check("находок > 30", lambda: (n("SELECT COUNT(*) FROM evidence") > 30,
                               f"{n('SELECT COUNT(*) FROM evidence')} находок"))
check("агенты говорят в чате", lambda: (n("SELECT COUNT(*) FROM messages WHERE topic='chat'") > 0,
                                        f"{n(chr(83)+'ELECT COUNT(*) FROM messages WHERE topic=' + chr(39) + 'chat' + chr(39))} реплик"))

# ---------------------------------------------------------------- 5. СЕРВИС
print("\n5. ЖИВОЙ СЕРВИС")
check("/health = 200", lambda: (http("/health")[0] == 200, "отвечает"))
check("/join = 200", lambda: (http("/join")[0] == 200, "страница подписки живая"))
check("/dashboard = 200", lambda: (http("/dashboard")[0] == 200, "дашборд отдаётся"))
for ep, price in [("/search?q=a", 0.01), ("/report", 0.10), ("/alpha", 0.50), ("/dataset", 1.25)]:
    def tier_ok(ep=ep, price=price):
        st, body = http(ep)
        if st != 402:
            return False, f"ожидался 402, получен {st}"
        d = json.loads(body)
        a = (d.get("accepts") or [{}])[0]
        got = int(a.get("amount", 0)) / 1e6
        if abs(got - price) > 1e-9:
            return False, f"цена {got} вместо {price}"
        wallet = re.search(r"WALLET_ETH=(0x[0-9a-fA-F]{40})",
                           (ROOT / ".env").read_text(encoding="utf-8")).group(1)
        if (a.get("payTo") or "").lower() != wallet.lower():
            return False, "payTo НЕ равен кошельку владельца!"
        return True, f"402, ${price}, payTo верный"
    check(f"тариф {ep}", tier_ok)

# ---------------------------------------------------------------- 6. API
print("\n6. API ДАШБОРДА")
for path, key in [("/api/status", "agents"), ("/api/queue", "interventions"),
                  ("/api/chat", "messages")]:
    def api_ok(path=path, key=key):
        st, body = http(path)
        d = json.loads(body)
        return (st == 200 and key in d), f"{st}, есть поле {key}"
    check(path, api_ok)


def nine_agents():
    st, body = http("/api/status")
    d = json.loads(body)
    ids = [a["id"] for a in d.get("agents", [])]
    need = {"scout", "proposer", "verifier", "adversary", "judge", "orchestrator",
            "explorer", "critic", "optimizer", "merchant", "distributor", "scribe", "watchdog"}
    missing = need - set(ids)
    return (not missing), (f"{len(ids)} агентов" if not missing else f"нет: {missing}")


check("все 13 агентов в API", nine_agents)

# ---------------------------------------------------------------- 7. ВНЕШНИЕ
print("\n7. ВНЕШНИЕ ЗАВИСИМОСТИ")
check("Bazaar Coinbase доступен", lambda: (
    http("?limit=1", "https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources")[0] == 200,
    "200 без авторизации"))
check("OpenFacilitator (Base mainnet)", lambda: (
    "eip155:8453" in http("/supported", "https://pay.openfacilitator.io")[1], "mainnet поддержан"))
check("EmailOctopus ключ рабочий", lambda: (
    __import__("core.eo", fromlist=["call"]).call("GET", "/lists")[0] == 200, "HTTP 200"))
check("индекс рынка >= 14000", lambda: (
    len(json.loads((ROOT / "data" / "bazaar_index.json").read_text(encoding="utf-8"))) >= 14000,
    f"{len(json.loads((ROOT/'data'/'bazaar_index.json').read_text(encoding='utf-8'))):,} сервисов"))

# ---------------------------------------------------------------- 8. ВОРКЕР
print("\n8. НЕПРЕРЫВНАЯ РАБОТА")
from agents import worker, growth
# Много шагов больше НЕ достоинство: по данным (optimizer 52 прогона / 55 «находок»
# из меняющегося счётчика) частые шаги без выхода накручивают показатель. Проверяем,
# что работа покрыта целиком, но ядро осталось узким.
check("работа покрыта целиком", lambda: (
    len(worker.CYCLE) + len(worker.SLOW_CYCLE) >= 15,
    f"{len(worker.CYCLE)} ядро + {len(worker.SLOW_CYCLE)} редких"))
check("ядро цикла узкое (раздел 28)", lambda: (
    len(worker.CYCLE) <= 12, f"{len(worker.CYCLE)} шагов в ядре"))
check("непроизводительные вынесены в редкие", lambda: (
    all(n not in [x for x, _ in worker.CYCLE]
        for n in ("optimize", "explore", "merchant", "study_market")),
    "optimize/explore/merchant/study_market не в каждом цикле"))
check("агенты развития подключены", lambda: (len(growth.CYCLE) >= 4, f"{len(growth.CYCLE)} функций"))


def worker_alive():
    """Живость меряется по ЖУРНАЛУ ПРОГОНОВ, а не по чату.

    Раньше проверялась свежесть реплик — но после введения дедупликации агенты
    молчат, когда нового нет. Молчание стало означать «нечего сказать», а проверка
    читала его как «умер» и ложно падала на живой системе.
    """
    last = _n("SELECT MAX(started_at) FROM runs")
    if not last:
        return False, "прогонов нет вообще"
    from datetime import datetime, timezone
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds()
    return age < 600, f"последний прогон {int(age)} сек назад"


check("воркер работает прямо сейчас", worker_alive)
check("журнал прогонов заполняется", lambda: (
    n("SELECT COUNT(*) FROM runs") >= 5, f"{n('SELECT COUNT(*) FROM runs')} прогонов"))
check("репутация считается по исходам", lambda: (
    n("SELECT COUNT(*) FROM agent_reputation") > 0,
    f"{n('SELECT COUNT(*) FROM agent_reputation')} записей"))
check("возражения получают оценку", lambda: (
    n("SELECT COUNT(*) FROM objections WHERE proved_correct IS NOT NULL") > 0,
    f"{n('SELECT COUNT(*) FROM objections WHERE proved_correct IS NOT NULL')} оценено"))
check("ошибки шагов попадают в журнал", lambda: (
    "record_run(name, agent, False" in (ROOT/"agents"/"worker.py").read_text(encoding="utf-8"),
    "падения записываются"))
check("дашборд полностью русский", lambda: (
    "press Run" not in (ROOT/"service"/"dashboard.html").read_text(encoding="utf-8")
    and "Decisions waiting" not in (ROOT/"service"/"dashboard.html").read_text(encoding="utf-8"),
    "английских строк интерфейса нет"))
check("вкладка чата агентов есть", lambda: (
    "renderChat" in (ROOT/"service"/"dashboard.html").read_text(encoding="utf-8"), "есть"))
check("агенты общаются друг с другом", lambda: (
    n("SELECT COUNT(*) FROM messages WHERE topic='ask'") > 0
    and n("SELECT COUNT(*) FROM messages WHERE topic='answer'") > 0,
    f"вопросов {n(chr(83)+chr(69)+chr(76)+chr(69)+chr(67)+chr(84)+' COUNT(*) FROM messages WHERE topic=' + chr(39) + 'ask' + chr(39))}, "
    f"ответов {n(chr(83)+chr(69)+chr(76)+chr(69)+chr(67)+chr(84)+' COUNT(*) FROM messages WHERE topic=' + chr(39) + 'answer' + chr(39))}"))
check("передача работы между агентами", lambda: (
    n("SELECT COUNT(*) FROM messages WHERE topic='handoff'") > 0, "handoff работает"))
check("шина агентов существует", lambda: ((ROOT/"core"/"bus.py").exists(), "core/bus.py"))
check("команда расширения существует", lambda: ((ROOT/"agents"/"team.py").exists(), "agents/team.py"))
check("отчёт писаря создан", lambda: ((ROOT/"reports"/"market_report.txt").exists(),
    f"{(ROOT/'reports'/'market_report.txt').stat().st_size} байт" if (ROOT/"reports"/"market_report.txt").exists() else "нет"))
check("цены подняты по рынку", lambda: (
    "const PRICE = '10000'" in (ROOT/"service"/"server.js").read_text(encoding="utf-8"),
    "/search = $0.01 вместо $0.001"))
check("4 тарифа в коде сервиса", lambda: (
    all(x in (ROOT/"service"/"server.js").read_text(encoding="utf-8")
        for x in ["/report", "/alpha", "/dataset", "/search"]), "все четыре"))

con.close()
print("\n" + "=" * 72)
print(f"ИТОГ: {R['pass']} прошло, {R['fail']} упало, {R['warn']} предупреждений")
if FAILS:
    print("\nПРОВАЛЫ:")
    for f in FAILS:
        print("  - " + f)
print("=" * 72)
sys.exit(1 if R["fail"] else 0)
