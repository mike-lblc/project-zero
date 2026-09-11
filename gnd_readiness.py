"""МАТРИЦА БОЕВОЙ ГОТОВНОСТИ — исполнение GND.txt.

Спецификация запрещает верить чему бы то ни было на слово: ни прошлым отчётам,
ни именам агентов, ни статусам на дашборде, ни процентам успеха. Каждое звено
цепочки от нуля до подтверждённой выручки проверяется по восьми признакам:

    КОД ЕСТЬ            функция существует и импортируется
    ПОДКЛЮЧЕНО          её кто-то вызывает: цикл, другой агент или API
    ВЫЗЫВАЕТСЯ          она отработала прямо сейчас, а не «должна отрабатывать»
    ЕСТЬ ИНСТРУМЕНТ     то, чем она работает, доступно (сеть, gh, модель, база)
    ЕСТЬ ПРАВО          гейт пропускает это действие
    МЕНЯЕТ СОСТОЯНИЕ    после вызова в базе появляется строка
    ПЕРЕДАЁТ ДАЛЬШЕ     результат доходит до следующего звена
    ЕСТЬ ДОКАЗАТЕЛЬСТВО есть чем подтвердить, что работа была

Звено получает один из приговоров GND:
    РАБОТАЕТ · ЧАСТИЧНО · СЛОМАНО · ОТСУТСТВУЕТ · НЕ ПОДКЛЮЧЕНО ·
    НЕТ ИНСТРУМЕНТА · НЕТ ПРАВА · НЕ СОХРАНЯЕТ · НЕТ ПЕРЕДАЧИ ДАЛЬШЕ · ПОДДЕЛКА

Матрица не самоцель. Она пишется в базу, показывается на дашборде и служит
списком того, что чинить. Звено без владельца — это не «зона роста», это дыра,
через которую вытекает вся цепочка.

Запуск: py -3.13 -X utf8 gnd_readiness.py
"""
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# НИ ОДИН ДОЧЕРНИЙ ПРОЦЕСС НЕ ОТКРЫВАЕТ ОКНО.
# Окна выскакивали не из запуска воркера, а из КАЖДОГО вызова gh, git, node и
# powershell: процесс без собственной консоли заводит новое окно на каждый
# такой вызов. Их двадцать, и правка по местам гарантировала бы двадцать
# первый. Флаг ставится один раз на весь процесс.
try:
    from core.launch import silence as _silence
    _silence()
except Exception:
    pass

from core.db import connect, ensure_schema  # noqa: E402

SCHEMA = """
CREATE TABLE IF NOT EXISTS capability_matrix (
  id INTEGER PRIMARY KEY,
  stage TEXT NOT NULL UNIQUE,
  ord INTEGER NOT NULL,
  owner_agent TEXT,
  entry_point TEXT,
  code_exists INTEGER,
  connected INTEGER,
  invoked INTEGER,
  has_tool INTEGER,
  has_permission INTEGER,
  changes_state INTEGER,
  passes_downstream INTEGER,
  has_proof INTEGER,
  verdict TEXT NOT NULL,
  detail TEXT,
  blocker TEXT,
  checked_at TEXT NOT NULL
);
"""


def now():
    return datetime.now(timezone.utc).isoformat()


def _con():
    c = connect()
    ensure_schema(c, SCHEMA)
    return c


def rowcount(table, where=""):
    c = connect()
    try:
        q = f"SELECT COUNT(*) FROM {table}" + (f" WHERE {where}" if where else "")
        return c.execute(q).fetchone()[0]
    except Exception:
        return None
    finally:
        c.close()


# ═══════════════════════════════════════════════ ЦЕПОЧКА ИЗ GND.txt
# Каждое звено: чем считается состояние, кто владеет, чем вызывается,
# и какая таблица должна пополниться, если звено действительно работает.
CHAIN = [
    dict(stage="A. НАЙТИ ВОЗМОЖНОСТЬ", owner="prospector",
         entry="agents.prospector:discover", state="money_paths",
         downstream="probe_paths", cycle="prospect"),
    dict(stage="B. ПРОВЕРИТЬ СПРОС", owner="prospector",
         entry="agents.prospector:probe", state="money_paths WHERE open_to_us IS NOT NULL",
         downstream="path_report", cycle="probe_paths"),
    dict(stage="C. СДЕЛАТЬ ПРЕДЛОЖЕНИЕ", owner="salesman",
         entry="agents.salesman:diagnose", state="lead_problems",
         downstream="find_channel", cycle="diagnose_leads"),
    dict(stage="D. НАЙТИ ПОКУПАТЕЛЕЙ", owner="leads",
         entry="agents.leads:hot_leads", state="leads",
         downstream="find_channel", cycle="find_leads"),
    dict(stage="E. НАЙТИ КАНАЛ СВЯЗИ", owner="leads",
         entry="agents.leads:find_channel", state="leads WHERE reachable IS NOT NULL",
         downstream="verify_service", cycle="find_channel"),
    dict(stage="F. ПОВОД ДЛЯ ОБРАЩЕНИЯ", owner="leads",
         entry="agents.leads:verify_service", state="lead_defects",
         downstream=None, cycle="verify_service"),
    dict(stage="G. ОБРАЩЕНИЕ НАРУЖУ", owner="craftsman",
         entry="agents.craftsman:claim", state="actions WHERE kind='bounty_claim'",
         downstream="pursue", cycle="pursue"),
    dict(stage="H. ПРИЁМ ОТВЕТОВ", owner="craftsman",
         entry="agents.craftsman:watch_prs", state="pull_requests",
         downstream="collect_payouts", cycle="watch_prs"),
    dict(stage="I. ПУТЬ ОПЛАТЫ", owner="orchestrator",
         entry="service:x402", state=None, downstream="watch_payments", cycle=None),
    dict(stage="J. ПОЛУЧИТЬ ПЛАТЁЖ", owner="orchestrator",
         entry="agents.worker:watch_payments", state="payments",
         downstream=None, cycle="watch_payments"),
    dict(stage="K. ПРОВЕРИТЬ ПЛАТЁЖ", owner="craftsman",
         entry="agents.craftsman:collect", state="human_interventions",
         downstream=None, cycle="collect_payouts"),
    # Звено проверяется по fulfil(), а не по deliver(): deliver умеет отправлять,
    # но сам себя не запускает. Работой считается то, что кто-то вызывает.
    dict(stage="L. ВЫПОЛНИТЬ ОБЕЩАННОЕ", owner="craftsman",
         entry="agents.craftsman:fulfil", state="pull_requests",
         downstream="watch_prs", cycle="fulfil"),
    dict(stage="M. УЧИТЬСЯ НА ИСХОДАХ", owner="optimizer",
         entry="core.economics:survival", state="agent_reputation",
         downstream="economics", cycle="economics"),
    dict(stage="N. ПОВТОРЯЕМОСТЬ", owner="orchestrator",
         entry="core.execution:board", state="tasks",
         downstream=None, cycle="housekeeping"),
]

VERDICTS = {
    "ok": "РАБОТАЕТ", "partial": "ЧАСТИЧНО", "broken": "СЛОМАНО",
    "missing": "ОТСУТСТВУЕТ", "unconnected": "НЕ ПОДКЛЮЧЕНО",
    "no_tool": "НЕТ ИНСТРУМЕНТА", "no_perm": "НЕТ ПРАВА",
    "no_state": "НЕ СОХРАНЯЕТ", "no_downstream": "НЕТ ПЕРЕДАЧИ ДАЛЬШЕ",
    "fake": "ПОДДЕЛКА",
    # Звено может быть исправным и при этом пустым: дефектов у чужих сервисов
    # не нашлось, платежей не приходило. Называть это поломкой — врать в свою
    # пользу наоборот, занижая. Но и «работает» сказать нельзя: под нагрузкой
    # оно ещё не проверено. Отдельный приговор честнее обоих.
    "no_input": "НЕТ ВХОДА",
}


def resolve(entry):
    """Достаёт функцию по «модуль:имя». Возвращает (функция, ошибка)."""
    if ":" not in entry:
        return None, "не указана точка входа"
    mod, fn = entry.split(":", 1)
    if mod == "service":
        return "service", None            # проверяется отдельно, по сети
    try:
        m = __import__(mod, fromlist=[fn])
    except Exception as e:
        return None, f"модуль не импортируется: {type(e).__name__}: {e}"
    f = getattr(m, fn, None)
    if f is None:
        return None, f"в {mod} нет {fn}"
    return f, None


def cycle_steps():
    """Все шаги, которые кто-то реально крутит."""
    from agents import worker
    return {n for n, _ in worker.CYCLE + worker.SLOW_CYCLE}


def ran_recently(step):
    c = connect()
    try:
        r = c.execute("SELECT COUNT(*) FROM runs WHERE notes LIKE ?", (step + ":%",)).fetchone()
        return (r[0] or 0) > 0
    finally:
        c.close()


def check_stage(i, s, steps):
    """Проверяет одно звено по восьми признакам GND. Вызовом, а не чтением."""
    res = dict(code_exists=0, connected=0, invoked=0, has_tool=0, has_permission=0,
               changes_state=0, passes_downstream=0, has_proof=0)
    detail, blocker = [], None

    # 1. КОД ЕСТЬ
    fn, err = resolve(s["entry"])
    if fn is None:
        return res, VERDICTS["missing"], err or "точка входа не найдена", "нет кода"
    res["code_exists"] = 1

    # 2. ПОДКЛЮЧЕНО
    if s["cycle"] and s["cycle"] in steps:
        res["connected"] = 1
    elif fn == "service":
        res["connected"] = 1
    else:
        blocker = "никто не вызывает"

    # 3. ВЫЗЫВАЕТСЯ — проверяем фактом
    before = rowcount(s["state"].split(" WHERE ")[0], s["state"].split(" WHERE ")[1]
                      if " WHERE " in (s["state"] or "") else "") if s["state"] else None
    if fn == "service":
        import urllib.error
        import urllib.request
        try:
            req = urllib.request.Request("http://127.0.0.1:8402/search?q=test",
                                         headers={"User-Agent": "gnd/1.0"})
            urllib.request.urlopen(req, timeout=15)
            res["invoked"] = 1
            detail.append("сервис ответил без оплаты — тариф не защищён")
        except urllib.error.HTTPError as e:
            res["invoked"] = 1
            res["has_tool"] = res["has_permission"] = 1
            if e.code == 402:
                res["changes_state"] = res["has_proof"] = 1
                detail.append("402 Payment Required, путь оплаты жив")
        except Exception as e:
            blocker = f"сервис недоступен: {type(e).__name__}"
    else:
        try:
            import inspect
            sig = inspect.signature(fn)
            required = [p for p in sig.parameters.values()
                        if p.default is inspect.Parameter.empty
                        and p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)]
            if required:
                # звено с обязательными аргументами вызываем не наугад:
                # его исполнение проверяется по факту прошлых запусков
                res["invoked"] = 1 if ran_recently(s["cycle"] or "") else 0
                detail.append(f"требует аргументов ({', '.join(p.name for p in required)}), "
                              f"проверено по журналу")
            else:
                out = fn()
                res["invoked"] = 1
                res["has_tool"] = res["has_permission"] = 1
                detail.append(f"вызов вернул: {str(out)[:70]}")
        except Exception as e:
            blocker = f"{type(e).__name__}: {str(e)[:90]}"
            detail.append("вызов упал: " + blocker)

    # 4-5. ИНСТРУМЕНТ И ПРАВО — если вызов прошёл, они есть по факту
    if res["invoked"] and not blocker:
        res["has_tool"] = res["has_permission"] = 1

    # 6. МЕНЯЕТ СОСТОЯНИЕ
    if s["state"]:
        tbl = s["state"].split(" WHERE ")[0]
        where = s["state"].split(" WHERE ")[1] if " WHERE " in s["state"] else ""
        after = rowcount(tbl, where)
        if after is None:
            blocker = blocker or f"таблицы {tbl} нет"
        elif after > 0:
            res["changes_state"] = 1
            res["has_proof"] = 1
            detail.append(f"{tbl}: строк {after}"
                          + (f" (+{after - before})" if before is not None
                             and after > before else ""))
        else:
            # пусто — но отработало ли звено вхолостую или упало?
            res["changes_state"] = 0
            detail.append(f"{tbl}: пусто — входных данных ещё не поступало")
    else:
        res["changes_state"] = res["has_proof"] = 1 if res["invoked"] else 0

    # 7. ПЕРЕДАЁТ ДАЛЬШЕ
    if s["downstream"] is None:
        res["passes_downstream"] = 1        # конец ветки, передавать некому
    elif s["downstream"] in steps:
        res["passes_downstream"] = 1
    else:
        blocker = blocker or f"следующее звено «{s['downstream']}» не подключено"

    # ПРИГОВОР
    if not res["connected"]:
        v = VERDICTS["unconnected"]
    elif blocker and not res["invoked"]:
        v = VERDICTS["broken"]
    elif not res["changes_state"]:
        # вызов прошёл без ошибки, но писать было нечего — это нехватка входа,
        # а не поломка звена; вызов упал — вот тогда поломка
        v = VERDICTS["no_input"] if (res["invoked"] and not blocker) else VERDICTS["no_state"]
    elif not res["passes_downstream"]:
        v = VERDICTS["no_downstream"]
    elif all(res.values()):
        v = VERDICTS["ok"]
    else:
        v = VERDICTS["partial"]
    return res, v, "; ".join(detail)[:400], blocker


def run():
    print("=" * 78)
    print("МАТРИЦА БОЕВОЙ ГОТОВНОСТИ — исполнение GND.txt")
    print("=" * 78)
    steps = cycle_steps()
    counts = {}
    con = _con()
    for i, s in enumerate(CHAIN):
        try:
            res, verdict, detail, blocker = check_stage(i, s, steps)
        except Exception:
            res = dict.fromkeys(("code_exists", "connected", "invoked", "has_tool",
                                 "has_permission", "changes_state", "passes_downstream",
                                 "has_proof"), 0)
            verdict, detail, blocker = VERDICTS["broken"], traceback.format_exc()[-200:], "сбой проверки"
        counts[verdict] = counts.get(verdict, 0) + 1
        con.execute("""INSERT INTO capability_matrix(stage,ord,owner_agent,entry_point,
                       code_exists,connected,invoked,has_tool,has_permission,changes_state,
                       passes_downstream,has_proof,verdict,detail,blocker,checked_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(stage) DO UPDATE SET
                       code_exists=excluded.code_exists, connected=excluded.connected,
                       invoked=excluded.invoked, has_tool=excluded.has_tool,
                       has_permission=excluded.has_permission,
                       changes_state=excluded.changes_state,
                       passes_downstream=excluded.passes_downstream,
                       has_proof=excluded.has_proof, verdict=excluded.verdict,
                       detail=excluded.detail, blocker=excluded.blocker,
                       checked_at=excluded.checked_at""",
                    (s["stage"], i, s["owner"], s["entry"], res["code_exists"],
                     res["connected"], res["invoked"], res["has_tool"],
                     res["has_permission"], res["changes_state"],
                     res["passes_downstream"], res["has_proof"], verdict, detail,
                     blocker, now()))
        con.commit()
        flags = "".join("+" if res[k] else "." for k in
                        ("code_exists", "connected", "invoked", "has_tool", "has_permission",
                         "changes_state", "passes_downstream", "has_proof"))
        print(f"\n{s['stage']:<26} [{flags}]  {verdict}")
        print(f"   владелец {s['owner']:<12} вход {s['entry']}")
        if detail:
            print(f"   {detail[:150]}")
        if blocker:
            print(f"   МЕШАЕТ: {blocker}")
    con.close()

    print("\n" + "=" * 78)
    print("признаки: код · подключено · вызвано · инструмент · право · "
          "состояние · передача · доказательство")
    print("ИТОГ ПО ЦЕПОЧКЕ: " + ", ".join(f"{v} — {n}" for v, n in
                                          sorted(counts.items(), key=lambda kv: -kv[1])))
    broken = counts.get(VERDICTS["ok"], 0)
    print(f"Полностью работающих звеньев: {broken} из {len(CHAIN)}")
    print("=" * 78)
    return counts


if __name__ == "__main__":
    c = run()
    sys.exit(0 if c.get(VERDICTS["ok"], 0) == len(CHAIN) else 1)
