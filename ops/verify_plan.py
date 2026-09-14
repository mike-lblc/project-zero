"""СВЕРКА ПЛАНА GND — каждое требование плана проверяется механически.

Зачем. Дважды за день доклад «всё сделано» оказывался неполным: построчное
чтение плана находило то, что проверки системы не ловили, а параллельный агент
нашёл старое имя состояния, которое пропустил мой же поиск. Сверка глазами
не воспроизводится и не падает. Этот файл падает.

Каждая проверка: раздел плана, требование его словами, функция, доказательство.
Поведение проверяется на временной базе, состояние — на живой, выдача
дашборда — живым запросом к службе (если служба не запущена, это говорится,
а не засчитывается).

    py -3.13 -X utf8 ops/verify_plan.py            быстрые проверки
    py -3.13 -X utf8 ops/verify_plan.py --full     плюс полный набор команд из раздела «Проверка»
"""
import ast
import json
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

RESULTS = []
CODE_GLOBS = ("agents/*.py", "core/*.py", "ops/*.py", "*.py", "service/*.js", ".github/workflows/*.yml")


def check(section, requirement):
    def deco(fn):
        try:
            ok, proof = fn()
        except Exception as e:
            ok, proof = False, f"{type(e).__name__}: {str(e)[:160]}"
        RESULTS.append((section, requirement, bool(ok), proof))
        return fn
    return deco


def src(rel):
    return (ROOT / rel).read_text(encoding="utf-8", errors="ignore")


def code_only(rel):
    """Код без комментариев и строк документации.

    В комментариях мы цитируем починенные поломки и запреты — «жёстко зашитый
    chain='base'», «проверять только 50 способов». Искать нарушение в тексте,
    который его описывает, значит объявить нарушителем объяснение.
    """
    text = src(rel)
    if str(rel).endswith(".py"):
        tree = ast.parse(text)
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            if isinstance(body, list) and body and isinstance(body[0], ast.Expr)                     and isinstance(getattr(body[0], "value", None), ast.Constant)                     and isinstance(body[0].value.value, str):
                body[0].value.value = ""
        return ast.unparse(tree)
    return "\n".join(l for l in text.splitlines() if not l.strip().startswith(("//", "#")))


def code_files():
    seen = []
    for g in CODE_GLOBS:
        for p in ROOT.glob(g):
            if "node_modules" not in p.parts and p not in seen:
                seen.append(p)
    return seen


class TempDB:
    """Временная база на время проверки; живая не трогается."""

    def __enter__(self):
        from core import db, execution, guard
        self.db, self.ex, self.guard = db, execution, guard
        self.saved = (db.DB_PATH, set(db._SCHEMA_DONE), db._WAL_SET,
                      execution._MIGRATED[0], guard.check_action)
        self.tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(self.tmp.name) / "plan.db"
        db._SCHEMA_DONE.clear()
        db._WAL_SET = False
        execution._MIGRATED[0] = False
        guard.check_action = lambda *a, **k: None
        db.init().close()
        execution._con().close()
        return self

    def __exit__(self, *exc):
        (self.db.DB_PATH, done, self.db._WAL_SET,
         self.ex._MIGRATED[0], self.guard.check_action) = self.saved
        self.db._SCHEMA_DONE.clear()
        self.db._SCHEMA_DONE.update(done)
        try:
            self.tmp.cleanup()
        except OSError:
            pass


def live(sql, args=()):
    from core.db import connect
    c = connect()
    try:
        return c.execute(sql, args).fetchall()
    finally:
        c.close()


def http_json(path):
    with urllib.request.urlopen(f"http://127.0.0.1:8402{path}", timeout=20) as r:
        return json.loads(r.read().decode("utf-8-sig"))


# ═══════════════════════════════════════════════ РЕШЕНИЯ ВЛАДЕЛЬЦА
@check("решения", "Конвейер заменяется целиком (не второй машиной рядом)")
def _():
    from core import execution as ex
    gnd = ["DISCOVERED", "QUALIFIED", "CONTACT_READY", "CONTACTED", "REPLIED", "NEGOTIATING",
           "AGREED", "WORKING", "QA", "DELIVERED", "PAYMENT_REQUESTED", "PAYMENT_PENDING",
           "PAID", "WITHDRAWABLE", "WITHDRAWN", "REJECTED", "FAILED", "EXTERNAL_BLOCKER"]
    legacy_rx = re.compile(r"state\s*(=|==|IN|in)\s*\(?\s*['\"](queued|running|done|failed|blocked|"
                           r"cancelled|in_progress)['\"]")
    hits = [f"{p.relative_to(ROOT)}:{src(p.relative_to(ROOT)).count(chr(10), 0, m.start()) + 1}"
            for p in code_files() for m in legacy_rx.finditer(src(p.relative_to(ROOT)))
            if p.name != "verify_plan.py" and "deep_checks" not in p.name]
    db_legacy = live("SELECT state, COUNT(*) FROM tasks WHERE state NOT IN (%s) GROUP BY state"
                     % ",".join("?" * len(gnd)), gnd)
    ok = sorted(ex.STATES) == sorted(gnd) and set(ex.INTERNAL) <= set(gnd) and not hits and not db_legacy
    return ok, (f"STATES = 18 имён GND; старых имён в коде {len(hits)} {hits[:3]}; "
                f"в базе {[tuple(r) for r in db_legacy]}")


@check("решения", "Услуги заводятся только исполнимые — каталог намерений не создаётся")
def _():
    from core import services
    bad = [(s["name"], services.check(s)) for s in services.CATALOG if services.check(s)]
    rows = live("SELECT name FROM services WHERE status='executable'")
    return (not bad and len(rows) == len(services.CATALOG)), (
        f"исполнимых в базе {len(rows)}, каждая прошла проверку исполнителя и оплаты; "
        f"не прошли: {bad or 'нет'}")


@check("решения", "Фильтр фиата остаётся: payout_reachable отбраковывает PayPal/Venmo/Zelle/банковский перевод")
def _():
    from agents import bounty
    words = {"paypal": "claiming payment: email us your paypal account",
             "venmo": "payout via venmo", "zelle": "we pay with zelle",
             "bank": "payout by bank transfer only"}
    got = {k: bounty.payout_reachable("x/y", f"bounty rules. {v}") for k, v in words.items()}
    return all(v is False for v in got.values()), f"ответы фильтра: {got}"


@check("решения", "Отступление от §3 записано в код комментарием и в отчёт отдельной строкой")
def _():
    in_code = "ОСОЗНАННОЕ ОТСТУПЛЕНИЕ" in src("agents/bounty.py").upper()
    report = ROOT / "reports" / "gnd-final.md"
    in_report = report.exists() and "отступление" in report.read_text(encoding="utf-8").lower()
    return in_code and in_report, f"в коде: {in_code}; в отчёте: {in_report}"


# ═══════════════════════════════════════════════ 1. МАРШРУТИЗАТОР ПЛАТЕЖЕЙ
@check("1", "Посев маршрутов: не меньше 18, 5 сетей, 5 валют; все непроверенные при заведении")
def _():
    with TempDB():
        from core import payment
        payment.seed()
        rs = payment.routes()
        nets = {r["network"] for r in rs if r["network"]}
        curs = {r["currency"] for r in rs if r["currency"]}
        verified = [r for r in rs if r["status"] == "verified"]
        ok = len(rs) >= 18 and len(nets) >= 5 and len(curs) >= 5 and not verified
        return ok, f"маршрутов {len(rs)}, сетей {len(nets)}, валют {len(curs)}, проверенных при посеве {len(verified)}"


@check("1", "reachable: «неизвестно», а не «нет» до проверки; «да» после; «нет» у закрытого")
def _():
    with TempDB():
        from core import payment
        payment.seed()
        before = payment.reachable(currency="USDC", network="polygon")[0]
        payment.mark("self-custody", network="polygon", status="verified")
        after = payment.reachable(currency="USDC", network="polygon")[0]
        blocked = payment.reachable(provider="bank_transfer")[0]
        return (before is None and after is True and blocked is False), (
            f"до проверки {before}, после {after}, банковский перевод {blocked}")


@check("1", "watch_payments обходит проверенные маршруты, а не chain='base' строкой")
def _():
    hard = [str(p.relative_to(ROOT)) for p in code_files()
            if p.suffix == ".py" and p.name != "verify_plan.py"
            and re.search(r"chain\s*=\s*['\"]base['\"]|\(\s*['\"]base['\"]\s*,\s*tx",
                          code_only(p.relative_to(ROOT)))]
    w = src("agents/worker.py")
    body = w[w.index("def watch_payments"):w.index("def refresh_market")]
    return (not hard and 'status"] == "verified"' in body), f"жёстких chain='base': {hard or 'нет'}"


@check("1", "Наблюдение за BTC-адресом владельца (второй адаптер)")
def _():
    from core import payment, payment_watch as w
    addr = "bc1qqwgyyqv6raq2jnghals2n2aujgwd4e9p64g4hr"
    return (payment.OWNER_DESTINATIONS.get("btc") == addr and "bitcoin" in w.TRANSFERS), \
        f"адрес в назначениях: {payment.OWNER_DESTINATIONS.get('btc') == addr}; обозреватель bitcoin: {'bitcoin' in w.TRANSFERS}"


@check("1", "Все поступления — через record_receipt: другой записи в payments нет")
def _():
    rx = re.compile(r"INSERT\s+(OR\s+\w+\s+)?INTO\s+payments\b", re.I)
    writers = sorted({str(p.relative_to(ROOT)) for p in code_files()
                      if rx.search(src(p.relative_to(ROOT))) and p.name != "verify_plan.py"})
    return writers == ["core\\payment.py"] or writers == ["core/payment.py"], f"пишут в payments: {writers}"


@check("1", "record_receipt: gross/fees/net, вид доказательства, повтор и платёж владельца отвергаются")
def _():
    with TempDB():
        from core import payment
        tx = "0x" + "cd" * 32
        r = payment.record_receipt(tx, "tx_hash", 10.0, "USDC", fees=0.5, network="base")
        dup = payment.record_receipt(tx, "tx_hash", 10.0, "USDC", network="base")
        errors = []
        for kwargs in ({"proof_kind": "подтверждено"}, {"from_party": payment.OWNER_DESTINATIONS["evm"]}):
            try:
                payment.record_receipt("0x" + "ef" * 32, kwargs.get("proof_kind", "tx_hash"), 5.0, "USDC",
                                       from_party=kwargs.get("from_party"))
                errors.append(f"принято: {kwargs}")
            except ValueError:
                pass
        return (r["net"] == 9.5 and not dup["ok"] and not errors), f"net {r['net']}, повтор {dup['ok']}, {errors or 'отказы на месте'}"


@check("1", "Маршрут проверяется по отдельности: сеть, контракт, минимум, комиссия, KYC, страна, срок, вывод")
def _():
    rows = live("SELECT currency, network, contract, minimum_payout, estimated_fee, kyc_required, "
                "country_eligibility, settlement_time, withdrawal_available FROM payment_routes "
                "WHERE status='verified'")
    missing = [f"{r[0]} {r[1]}" for r in rows
               if None in (r[3], r[4], r[5], r[6], r[7], r[8])
               or (r[0] in ("USDC", "USDC.e") and not r[2])]
    return (rows and not missing), f"проверенных {len(rows)}, с пустыми полями: {missing or 'нет'}"


# ═══════════════════════════════════════════════ 2. КОНВЕЙЕР СДЕЛКИ
@check("2", "Доказательства встроены в _transition для 8 переходов; без доказательства — отказ")
def _():
    from core import execution as ex
    need = {"CONTACTED", "REPLIED", "AGREED", "DELIVERED", "PAYMENT_REQUESTED", "PAID",
            "WITHDRAWABLE", "WITHDRAWN"}
    refused = []
    with TempDB():
        path = [("QUALIFIED", "оценена"), ("CONTACT_READY", "канал найден"),
                ("CONTACTED", "https://github.com/o/r/issues/1"),
                ("REPLIED", "https://github.com/o/r/issues/1#issuecomment-2"),
                ("AGREED", "справочник команд, цена $25, оплата USDC после слияния"),
                ("WORKING", "начата"), ("QA", "сверена"),
                ("DELIVERED", "https://github.com/o/r/pull/3"),
                ("PAYMENT_REQUESTED", "25 USDC в сети base на 0xECa891e34b3E5873181Fb779672564E198C55354"),
                ("PAID", "0x" + "ab" * 32),
                ("WITHDRAWN", "0x" + "cd" * 32)]
        tid = ex.open_deal("проверка доказательств", "техническая документация", "craftsman", "шаг", [])
        for state, good in path:
            if state in need:
                for weak in ("", "готово", "отправили письмо клиенту сегодня"):
                    try:
                        ex.advance(tid, state, weak)
                        return False, f"{state} принят с доказательством «{weak}»"
                    except (ex.MissingEvidence, ex.InvalidTransition):
                        pass
                refused.append(state)
            ex.advance(tid, state, good)
    return set(refused) >= need - {"WITHDRAWABLE"} and set(ex.EVIDENCE_REQUIRED) == need, (
        f"отвергнуты пустое, «готово» и пересказ для: {refused}")


@check("2", "Прыжок через состояния отвергается (DISCOVERED → PAID)")
def _():
    from core import execution as ex
    with TempDB():
        tid = ex.open_deal("прыжок", "баунти за код", "bounty", "шаг", [])
        try:
            ex.advance(tid, "PAID", "0x" + "ab" * 32)
            return False, "прыжок принят"
        except ex.InvalidTransition:
            return True, "DISCOVERED → PAID запрещён машиной состояний"


@check("2", "Запреты инвариантами: черновик ≠ отправка, лид ≠ клиент, 402 ≠ платёж, невыводимое ≠ прибыль")
def _():
    from core import regressions
    names = ["черновик не является отправкой", "лид не является клиентом",
             "ответ 402 не является платежом", "невыводимое начисление не является прибылью"]
    ok, bad, fails, _ = regressions.run(verbose=False)
    have = {r[0] for r in live("SELECT name FROM invariants")}
    failing = [f[0] for f in fails if f[0] in names]
    return (all(n in have for n in names) and not failing), f"в каталоге: {[n in have for n in names]}, падают: {failing or 'нет'}"


@check("2", "Реальные дела проходят через конвейер: обращения и заявки — сделки с доказательством")
def _():
    sent = live("SELECT domain, task_id FROM outreach WHERE url IS NOT NULL")
    orphan = [d for d, t in sent if not t]
    deals = live("SELECT COUNT(*) FROM tasks WHERE kind='deal' AND revenue_method IS NOT NULL")[0][0]
    return (not orphan and deals >= len(sent)), f"обращений {len(sent)}, без сделки {orphan or 'нет'}; сделок {deals}"


# ═══════════════════════════════════════════════ 3. УСЛУГИ
@check("3", "Таблица services с одиннадцатью обязательными полями")
def _():
    from core import services
    cols = {r[1] for r in live("PRAGMA table_info(services)")}
    need = {"buyer", "problem", "find_client", "contact", "offer", "acceptance", "executor",
            "qa", "delivery", "payment", "proof"}
    return need <= cols and set(services.REQUIRED) == need, f"нет полей: {need - cols or 'все есть'}"


@check("3", "Все 29 услуг §5–6 учтены: исполнима или «пока не умеем» с причиной")
def _():
    from core import services
    names = {r[0] for r in live("SELECT name FROM services")}
    uncovered = [g for g, n in services.GND_SERVICES.items() if n not in names]
    no_reason = [r[0] for r in live("SELECT name FROM services WHERE status='not_yet' "
                                    "AND (reason IS NULL OR length(reason) < 15)")]
    return (len(services.GND_SERVICES) == 29 and not uncovered and not no_reason), (
        f"услуг в директиве {len(services.GND_SERVICES)}; не учтены {uncovered or 'нет'}; без причины {no_reason or 'нет'}")


@check("3", "Продавец предлагает с ценой только исполнимые услуги; невыполнимое — без цены и с причиной")
def _():
    from agents import salesman
    status = dict(live("SELECT name, status FROM services"))
    unmapped = set(salesman.OFFERS) - set(salesman.OFFER_SERVICE)
    wrong = []
    for code, svc in salesman.OFFER_SERVICE.items():
        offer, price = salesman.offer_for(code)
        executable = status.get(svc) == "executable"
        if executable and price <= 0:
            wrong.append(f"{code}: исполнимая услуга без цены")
        if not executable and (price > 0 or not offer.startswith("пока не умеем")):
            wrong.append(f"{code}: предложено невыполнимое за ${price}")
    priced = live("SELECT problem, COUNT(*) FROM lead_problems WHERE price_usd > 0 GROUP BY problem")
    bad_rows = [p for p, _ in priced if status.get(salesman.OFFER_SERVICE.get(p)) != "executable"]
    return (not unmapped and not wrong and not bad_rows), (
        f"без услуги: {unmapped or 'нет'}; неверных предложений: {wrong or 'нет'}; "
        f"цен в базе у невыполнимого: {bad_rows or 'нет'}")


# ═══════════════════════════════════════════════ 4. АГЕНТЫ
NEW_AGENTS = ("closer", "verifier", "collector", "channel_manager")


@check("4", "closer, verifier, payment_collector, channel_manager — в составе")
def _():
    from core import roster  # noqa: F401
    from core.agent import REGISTRY
    return all(a in REGISTRY for a in NEW_AGENTS), f"в составе: {[a for a in NEW_AGENTS if a in REGISTRY]}"


@check("4", "У каждого: промпт с общей целью, инструменты в белом списке, KPI, права")
def _():
    from core import roster, guard
    from core.agent import REGISTRY, TOOLS
    order = {"GREEN": 0, "YELLOW": 1, "RED": 2, "BLACK": 3}
    problems = []
    for n in NEW_AGENTS:
        a = REGISTRY[n]
        if roster.MISSION.strip()[:60] not in a.system:
            problems.append(f"{n}: нет общей цели")
        if not a.kpi or len(a.kpi) < 30:
            problems.append(f"{n}: KPI пуст")
        for t in a.tools:
            if t not in TOOLS:
                problems.append(f"{n}: инструмент {t} не объявлен")
            elif order[TOOLS[t].action_class] > order[a.max_class] and not guard.standing(t):
                # выше предела агента можно только по постоянному разрешению владельца;
                # без него инструмент в списке — мёртвая запись, модель выберет его и получит отказ
                problems.append(f"{n}: {t} выше прав агента и без постоянного разрешения")
    return not problems, f"проблем: {problems or 'нет'}"


@check("4", "У каждого: шаг в цикле, облачный запуск, подписка на событие, тест в evals/")
def _():
    from agents import worker
    from core import events
    steps = {name for name, _ in worker.CYCLE + worker.SLOW_CYCLE}
    tests = " ".join(p.read_text(encoding="utf-8") for p in (ROOT / "evals").glob("test_*.py"))
    problems = []
    for n in NEW_AGENTS:
        mine = [s for s, a in worker.AGENT_OF.items() if a == n and s in steps]
        if not mine:
            problems.append(f"{n}: нет шага в цикле")
        if mine and not any(s in worker.CLOUD_STEPS for s in mine):
            problems.append(f"{n}: шаги не запускаются в облаке")
        if not any(sub[0] == n for sub in events.SUBSCRIPTIONS.values()):
            problems.append(f"{n}: не подписан ни на одно событие")
        handlers = [k for k, (who, _) in events.SUBSCRIPTIONS.items() if who == n]
        if any(worker.EVENT_HANDLER.get(k) not in steps for k in handlers):
            problems.append(f"{n}: событие без шага-обработчика")
    for mod in ("agents.closer", "agents.collector", "verify_evidence", "channel_health"):
        if mod.split(".")[-1] not in tests:
            problems.append(f"нет теста: {mod}")
    return not problems, f"проблем: {problems or 'нет'}"


@check("4", "channel_manager поглощает postman")
def _():
    from agents import worker
    left = [s for s, a in worker.AGENT_OF.items() if a == "postman"]
    runs = live("SELECT COUNT(*) FROM runs WHERE agent='postman'")[0][0]
    return (not left and runs == 0), f"шагов за postman {left or 'нет'}; прогонов под этим именем {runs}"


@check("4", "Сопоставление поступления с запросом (PAID) запускается в цикле, а не только вручную")
def _():
    from agents import worker
    w = src("agents/worker.py")
    body = w[w.index("def watch_payments"):w.index("def refresh_market")]
    steps = {name for name, _ in worker.CYCLE + worker.SLOW_CYCLE}
    return ("collect" in body or "match_receipts" in body or "collect_payments" in steps), \
        f"watch_payments сопоставляет: {'collect' in body or 'match_receipts' in body}; шаг collect_payments: {'collect_payments' in steps}"


@check("4", "Каждый агент получает слово: очередь рассуждения по журналу, а не по памяти процесса")
def _():
    from agents import worker
    with TempDB() as t:
        from core import agent
        agent._con().close()
        c = t.db.connect()
        c.execute("INSERT INTO agent_decisions(agent,state_seen,chose,why,allowed,outcome,ok,model,decided_at) "
                  "VALUES ('adversary','{}','x','',1,'',1,'m','2026-09-13T12:00:00')")
        c.commit(); c.close()
        first = worker.next_reasoner(["adversary", "watchdog"])
    stale = live("SELECT agent, MAX(decided_at) FROM agent_decisions GROUP BY agent "
                 "HAVING MAX(decided_at) < strftime('%Y-%m-%dT%H:%M:%S','now','-12 hours')")
    return first == "watchdog", (f"после «перезапуска» слово получает давно молчавший: {first}; "
                                 f"агентов без решения дольше 12 ч сейчас: {len(stale)}")


@check("4", "Каждый шаг цикла приписан хозяину — владельцу своего инструмента")
def _():
    from agents import worker
    from core import roster  # noqa: F401
    from core.agent import REGISTRY
    steps = [n for n, _ in worker.CYCLE + worker.SLOW_CYCLE]
    owners = {}
    for name, a in REGISTRY.items():
        for tool_name in a.tools:
            owners.setdefault(tool_name, set()).add(name)
    missing = [st for st in steps if st not in worker.AGENT_OF]
    wrong = [st for st in steps if st in owners and worker.AGENT_OF.get(st) not in owners[st]]
    return not missing and not wrong, f"без хозяина: {missing or 'нет'}; не владельцу инструмента: {wrong or 'нет'}"


# ═══════════════════════════════════════════════ 5. РЕЕСТР СПОСОБОВ
@check("5", "Ключ «партнёрские отчисления» один; 50 способов GND сведены к классам без потерь")
def _():
    from agents import prospector as p
    s = src("agents/prospector.py")
    block = s[s.index("CATEGORIES = {"):s.index("\n}", s.index("CATEGORIES = {"))]
    keys = re.findall(r'^\s+"([^"]+)":\s*\[', block, re.M)
    lost = [n for n, c in p.GND_METHODS.items() if c and c not in p.CATEGORIES]
    return (keys.count("партнёрские отчисления") == 1 and len(keys) == len(p.CATEGORIES)
            and sorted(p.GND_METHODS) == list(range(1, 51)) and not lost), (
        f"классов {len(p.CATEGORIES)}, повторов ключей {len(keys) - len(set(keys))}, способов без класса {lost or 'нет'}")


@check("5", "Искусственного потолка «проверять только N способов» в коде нет; найденное рынком сохраняется")
def _():
    s = code_only("agents/prospector.py")
    caps = re.findall(r"(только\s+\d+\s+способ|CATEGORIES\)\[:\d+\]|list\(CATEGORIES\)\[:\d+\])", s)
    cols = {r[1] for r in live("PRAGMA table_info(path_categories)")}
    return (not caps and "queries" in cols), f"потолков: {caps or 'нет'}; найденные классы в базе: {'queries' in cols}"


# ═══════════════════════════════════════════════ 6. ДАШБОРД
@check("6", "Расхождение 24/14 исчезло: состав в API и сцене из реестра, призраков нет, verifier не читает чужое")
def _():
    from core import roster  # noqa: F401
    from core.agent import REGISTRY
    srv = code_only("service/server.js")
    try:
        api = {a["id"] for a in http_json("/api/status")["agents"]}
    except Exception as e:
        return False, f"служба не ответила ({type(e).__name__}) — выдачу не проверить"
    scene = set(re.findall(r'\{id:"([a-z_]+)"', src("service/dashboard.html")))
    return (set(REGISTRY) <= api and not (scene - api) and "frontier-escalation" not in srv
            and "{ id: 'scout'" not in srv), (
        f"состав {len(REGISTRY)} в API: {set(REGISTRY) <= api}; призраки сцены: {scene - api or 'нет'}")


@check("6", "Разбивка по способу: найдено … выведено, gross/fees/net, валюта, провайдер, сеть, доказательство, блокер, время события")
def _():
    try:
        rv = http_json("/api/execution")["revenue"]
    except Exception as e:
        return False, f"служба не ответила ({type(e).__name__})"
    d = rv.get("deals_by_method") or []
    dash = src("service/dashboard.html")
    labels = ["найдено", "квалифицировано", "обращений", "ответов", "договорённостей", "в работе",
              "сдано", "запрошено оплат", "оплачено", "к выводу", "выведено", "gross", "fees", "net",
              "доказательство", "дальше", "последнее событие"]
    missing = [l for l in labels if l not in dash]
    fields = d and all(k in d[0] for k in ("reached", "money", "evidence", "blocker", "last_event"))
    return (bool(d) and fields and not missing and "last_cloud_event" in rv), (
        f"способов со сделками {len(d)}; подписей нет: {missing or 'все есть'}")


@check("6", "Base/USDC не основная сеть: показано распределение по всем маршрутам")
def _():
    try:
        routes = http_json("/api/execution")["revenue"]["routes"]
    except Exception as e:
        return False, f"служба не ответила ({type(e).__name__})"
    nets = {r["network"] for r in routes if r["status"] == "verified"}
    return len(nets) >= 5 and "Маршруты оплаты" in src("service/dashboard.html"), f"проверенные сети в выдаче: {sorted(nets)}"


# ═══════════════════════════════════════════════ 7. ПРИОРИТЕТ
@check("7", "Формула §9 в bounty (fit_score) и warroom; валюту и сеть в расчёт не передать")
def _():
    from core import priority
    banned = {"currency", "network", "chain", "provider", "crypto", "adapter"}
    fields = set(priority.Estimate.__dataclass_fields__)
    b, w = src("agents/bounty.py"), src("agents/warroom.py")
    return ("priority.score" in b and "priority.score" in w and not fields & banned), (
        f"bounty: {'priority.score' in b}; warroom: {'priority.score' in w}; запрещённых полей {fields & banned or 'нет'}")


@check("7", "При равенстве — порядок директивы (объявленный бюджет, быстрая приёмка…) в рабочем отборе")
def _():
    # Рабочий отбор — то, что решает, за какую задачу браться. Отчёты и выдача
    # дашборда лишь показывают список и порядком работы не управляют.
    rx = re.compile(r"ORDER BY fit_score DESC(?!\s*,\s*declared DESC)", re.I)
    plain = [f"{rel}:{src(rel).count(chr(10), 0, m.start()) + 1}"
             for rel in ("agents/bounty.py", "agents/craftsman.py")
             for m in rx.finditer(src(rel))]
    return not plain, f"отборов без разбора равенства: {plain or 'нет'}"


@check("7", "Выплата в приоритете — из маршрутизатора: платформа без проверенного маршрута не «дойдёт»")
def _():
    from agents import bounty
    ans = bounty.payout_reachable("x/y", "bounty rules: payout via algora")
    return ans is not True, f"Algora (маршрут не проверен) → {ans}"


# ═══════════════════════════════════════════════ 8. ОТЧЁТ
@check("8", "Отчёт §16: 15 столбцов и 10 отдельных списков, собран из базы")
def _():
    t = (ROOT / "reports" / "gnd-final.md").read_text(encoding="utf-8")
    cols = ["Revenue method", "Opportunity source", "Buyer", "Agent owner", "Execution capability",
            "Contact channel", "Delivery channel", "Payment options", "Payout accessibility",
            "Current state", "Evidence", "Expected net value", "Time to cash", "Blocker", "Next action"]
    secs = ["Действительно подключены", "Только исследованы", "Реальные заявки",
            "Реальные отправленные сообщения", "Ответы", "Выполненная работа", "Начисление",
            "Доступный вывод", "Подтверждённая прибыль", "Новые способы"]
    return all(c in t for c in cols) and all(s in t for s in secs), (
        f"столбцов {sum(c in t for c in cols)}/15, списков {sum(s in t for s in secs)}/10")


# ═══════════════════════════════════════════════ ПРОВЕРКА
@check("проверка", "Каждый найденный дефект закреплён инвариантом — каталог растёт")
def _():
    have = {r[0] for r in live("SELECT name FROM invariants")}
    need = ["признание токена по контракту, а не по символу", "поиск различает поломку и пустоту",
            "проверяющий читает настоящие доказательства", "ответ — только после нашего сообщения",
            "попытка с преемником закрывается", "дашборд считает поступления, а не пустую таблицу",
            "слитый PR опознаётся в любом регистре", "маскировка под браузер запрещена",
            "событие с приостановленным обработчиком не берётся",
            "доказательство сделки проверяется по форме",
            "выплата оценивается маршрутизатором, а не списком слов",
            "продавец предлагает только исполнимые услуги",
            "слово получает давно молчавший агент",
            "запись в Moltbook решает проверочную задачу сама",
            "предохранитель Moltbook срабатывает ниже порога блокировки",
            "решатель Moltbook не гадает, когда не уверен"]
    missing = [n for n in need if n not in have]
    return not missing, f"инвариантов {len(have)}; не заведены: {missing or 'нет'}"


@check("проверка", "Воркер на новом коде работает без ошибок")
def _():
    start = live("SELECT MAX(started_at) FROM runs WHERE notes LIKE 'worker_start%'")[0][0]
    rows = live("SELECT COUNT(*), SUM(status='error') FROM runs WHERE started_at > ?", (start,))
    n, err = rows[0][0], rows[0][1] or 0
    return (n > 0 and err == 0), f"шагов после старта {start[:16] if start else '—'}: {n}, с ошибкой {err}"


def run_cmd(args, timeout=900):
    r = subprocess.run(["py", "-3.13", "-X", "utf8"] + args, cwd=ROOT, capture_output=True,
                       text=True, encoding="utf-8", errors="ignore", timeout=timeout)
    return r.returncode, r.stdout + r.stderr


if "--full" in sys.argv:
    for args, rx in ((["audit.py"], r"ИТОГ: \d+ прошло, 0 упало"),
                     (["fake_work_audit.py"], r"ИТОГ: \d+ прошло, 0 упало"),
                     (["core/regressions.py"], r"ИТОГ: \d+ прошло, 0 упало"),
                     (["-m", "pytest", "evals/", "-q"], r"\d+ passed(?!.*failed)"),
                     (["agent_anatomy.py"], r"отсутствует 0"),
                     (["verify_claims.py"], r"НЕ подтверждено 0"),
                     # критический путь обязан обрываться не раньше шага 6: всё до
                     # «выплата объявлена нам» находится на нашей стороне
                     (["ops/deep_checks.py"], r"ОБРЫВАЕТСЯ НА: [67]\."),
                     (["ops/prove_receipt.py"], r"неисправно 0"),
                     (["mtbx_audit.py"], r"ИТОГ: \d+ прошло, 0 упало")):
        @check("проверка", "команда " + " ".join(args))
        def _(args=args, rx=rx):
            code, out = run_cmd(args)
            line = next((l for l in reversed(out.splitlines()) if re.search(r"ИТОГ|passed|failed|неисправно|ОБРЫВАЕТСЯ", l)), out[-120:])
            return bool(re.search(rx, out)), line.strip()[:150]


if __name__ == "__main__":
    print("=" * 78)
    print("СВЕРКА ПЛАНА GND — каждое требование механически")
    print("=" * 78)
    cur = None
    for section, req, ok, proof in RESULTS:
        if section != cur:
            print(f"\n── {section}")
            cur = section
        print(f"  [{'ДА ' if ok else 'НЕТ'}] {req}")
        print(f"        {proof}")
    bad = [r for r in RESULTS if not r[2]]
    print("\n" + "=" * 78)
    print(f"ИТОГ: выполнено {len(RESULTS) - len(bad)} из {len(RESULTS)}")
    if bad:
        print("НЕ ВЫПОЛНЕНО:")
        for s, r, _, p in bad:
            print(f"  - [{s}] {r}")
    sys.exit(1 if bad else 0)
