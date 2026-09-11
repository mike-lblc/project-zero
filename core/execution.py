"""ЯДРО ИСПОЛНЕНИЯ — разделы 4, 14, 15, 22, 28, 31, 38, 39, 42, 43 спецификации EXXX.

Диагноз, поставленный аудитом: 3 710 строк кода и ни одного конвейера задач.
Система умела обсуждать, измерять и рисовать — но у неё не было места, где
задача проходит путь от «надо» до «сделано и доказано».

Здесь этот путь появляется:

  задача -> состояние -> исполнение -> ДОКАЗАТЕЛЬСТВО -> закрытие
  провал -> НЕ конец, а порождение следующей попытки (раздел 31)

Главные правила, вшитые в код, а не в промпт:

  * задачу нельзя закрыть без доказательства работы (раздел 39, страж завершения)
  * задача обязана нести следующее ИСПОЛНИМОЕ действие (раздел 4)
  * провал обязан породить следующую попытку или явный блокер (раздел 31)
  * блокер засчитывается ТОЛЬКО из списка раздела 5 — «мне надо подумать»
    блокером не является (раздел 42, запрет ложной автономии)
  * перед объявлением блокера обязателен обход возможностей (раздел 17)
"""
import sys, json, hashlib
from pathlib import Path
from datetime import datetime, timezone, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.db import connect, ensure_schema

# Раздел 38: машина состояний. Переходы разрешены только по этой таблице.
STATES = {
    "queued":    {"running", "cancelled"},
    "running":   {"done", "failed", "blocked"},
    "failed":    {"queued", "blocked", "cancelled"},   # провал -> следующая попытка
    "blocked":   {"queued", "cancelled"},              # блокер снят -> обратно в очередь
    "done":      set(),                                # done окончателен
    "cancelled": set(),
}

# Раздел 5: ТОЛЬКО это считается настоящим блокером.
REAL_BLOCKERS = {
    "missing_account_authorization",
    "missing_api_credentials",
    "human_approval_required",
    "external_service_unavailable",
    "captcha_or_human_verification",
    "legal_or_business_identity_required",
    "payment_account_unavailable",
    "private_resource_inaccessible",
    "irreversible_high_risk_action",
}

# Раздел 42: это НЕ блокеры, это задачи. Попытка объявить их блокером отклоняется.
FAKE_BLOCKERS = ("need to decide", "need to research", "need to find", "need to analyze",
                 "need to write", "need to create", "need another agent", "надо решить",
                 "надо изучить", "надо найти", "надо подумать", "нужен ещё агент")

# Раздел 17: что обязан перебрать агент, прежде чем сказать «не могу».
CAPABILITY_CHECKLIST = [
    "собственные инструменты",
    "инструменты экосистемы",
    "возможности других агентов",
    "существующие интеграции",
    "бесплатные и открытые методы",
    "доступное через браузер",
    "детерминированный код",
    "уже имеющиеся данные и активы",
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY,
  objective TEXT NOT NULL,
  next_action TEXT NOT NULL,        -- раздел 4: следующее ИСПОЛНИМОЕ действие
  owner_agent TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'queued',
  money_proximity INTEGER NOT NULL DEFAULT 3,  -- раздел 23: 1 = ближе всего к деньгам
  attempts INTEGER NOT NULL DEFAULT 0,
  parent_id INTEGER REFERENCES tasks(id),
  blocker_kind TEXT,
  blocker_detail TEXT,
  capability_check TEXT,            -- раздел 17: что перебрали до блокера
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS proof_of_work (
  id INTEGER PRIMARY KEY,
  task_id INTEGER NOT NULL REFERENCES tasks(id),
  kind TEXT NOT NULL,               -- file | http | db_row | external_id | measurement
  reference TEXT NOT NULL,          -- путь, URL, id записи — что можно проверить
  detail TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS task_events (
  id INTEGER PRIMARY KEY,
  task_id INTEGER NOT NULL REFERENCES tasks(id),
  from_state TEXT,
  to_state TEXT NOT NULL,
  note TEXT,
  at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_state ON tasks(state, money_proximity);
"""


class InvalidTransition(Exception): pass
class NoProof(Exception): pass
class FakeBlocker(Exception): pass


def now():
    return datetime.now(timezone.utc).isoformat()


def _con():
    c = connect()
    ensure_schema(c, SCHEMA)
    return c


# ---------------------------------------------------------------- создание
def create(objective, next_action, owner_agent, money_proximity=3, parent_id=None):
    """Раздел 4: задача без следующего ИСПОЛНИМОГО действия не создаётся.

    money_proximity (раздел 23): 1 = деньги напрямую, 5 = далеко от денег.
    Очередь всегда отдаёт то, что ближе к деньгам.
    """
    if not next_action or not next_action.strip():
        raise ValueError("задача без next_action не принимается: это не задача, а пожелание")
    if not (1 <= int(money_proximity) <= 5):
        raise ValueError("money_proximity должен быть от 1 до 5")
    c = _con()
    tid = c.execute("""INSERT INTO tasks(objective,next_action,owner_agent,money_proximity,
                       parent_id,created_at,updated_at) VALUES (?,?,?,?,?,?,?)""",
                    (objective, next_action, owner_agent, int(money_proximity),
                     parent_id, now(), now())).lastrowid
    c.execute("INSERT INTO task_events(task_id,from_state,to_state,note,at) VALUES (?,?,?,?,?)",
              (tid, None, "queued", "создана", now()))
    c.commit(); c.close()
    return tid


# ---------------------------------------------------------------- переходы
def _transition(task_id, to_state, note=None):
    c = _con()
    row = c.execute("SELECT state, attempts FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not row:
        c.close(); raise ValueError(f"нет задачи #{task_id}")
    cur = row[0]
    if to_state not in STATES.get(cur, set()):
        c.close()
        raise InvalidTransition(f"переход {cur} -> {to_state} запрещён машиной состояний")
    attempts = row[1] + (1 if to_state == "running" else 0)
    c.execute("UPDATE tasks SET state=?, attempts=?, updated_at=? WHERE id=?",
              (to_state, attempts, now(), task_id))
    c.execute("INSERT INTO task_events(task_id,from_state,to_state,note,at) VALUES (?,?,?,?,?)",
              (task_id, cur, to_state, note, now()))
    c.commit(); c.close()
    return to_state


def start(task_id, note=None):
    return _transition(task_id, "running", note)


# ---------------------------------------------------------------- доказательство
def add_proof(task_id, kind, reference, detail=None):
    """Раздел 15: доказательство — это то, что можно ПРОВЕРИТЬ независимо:
    файл, URL, строка в базе, внешний идентификатор, измерение."""
    if kind not in ("file", "http", "db_row", "external_id", "measurement"):
        raise ValueError(f"неизвестный вид доказательства: {kind}")
    if not reference or not str(reference).strip():
        raise ValueError("доказательство без ссылки не принимается")
    c = _con()
    pid = c.execute("INSERT INTO proof_of_work(task_id,kind,reference,detail,created_at) "
                    "VALUES (?,?,?,?,?)", (task_id, kind, str(reference), detail, now())).lastrowid
    c.commit(); c.close()
    return pid


def complete(task_id, note=None):
    """Раздел 39, страж завершения: без доказательства закрыть нельзя."""
    c = _con()
    n = c.execute("SELECT COUNT(*) FROM proof_of_work WHERE task_id=?", (task_id,)).fetchone()[0]
    c.close()
    if n == 0:
        raise NoProof(f"задачу #{task_id} нельзя закрыть: нет ни одного доказательства работы")
    return _transition(task_id, "done", note or f"закрыта, доказательств: {n}")


# ---------------------------------------------------------------- провал
def fail(task_id, reason, next_action=None, money_proximity=None):
    """Раздел 31: провал обязан породить СЛЕДУЮЩУЮ ПОПЫТКУ, а не закончиться.

    Если следующая попытка не названа — задача уходит в failed и остаётся висеть,
    что видно в отчётах. Молча умереть она не может.
    """
    _transition(task_id, "failed", reason)
    if not next_action:
        return None
    c = _con()
    row = c.execute("SELECT objective, owner_agent, money_proximity FROM tasks WHERE id=?",
                    (task_id,)).fetchone()
    c.close()
    return create(row[0], next_action, row[1],
                  money_proximity or row[2], parent_id=task_id)


# ---------------------------------------------------------------- блокер
def block(task_id, kind, detail, capability_check=None):
    """Раздел 5 + 17 + 42: блокером считается только настоящий блокер,
    и только после обхода возможностей."""
    if kind not in REAL_BLOCKERS:
        raise FakeBlocker(
            f"«{kind}» не блокер. Настоящие: {', '.join(sorted(REAL_BLOCKERS))}. "
            f"Всё остальное — задача, её надо исполнить.")
    low = (detail or "").lower()
    for fake in FAKE_BLOCKERS:
        if fake in low:
            raise FakeBlocker(f"«{detail}» — это задача, а не блокер (раздел 42)")
    missing = [x for x in CAPABILITY_CHECKLIST if x not in (capability_check or [])]
    if missing:
        raise FakeBlocker(
            f"нельзя объявить блокер, не перебрав возможности (раздел 17). "
            f"Не проверено: {', '.join(missing[:4])}")
    c = _con()
    c.execute("UPDATE tasks SET blocker_kind=?, blocker_detail=?, capability_check=? WHERE id=?",
              (kind, detail, json.dumps(capability_check, ensure_ascii=False), task_id))
    c.commit(); c.close()
    return _transition(task_id, "blocked", f"{kind}: {detail}")


def unblock(task_id, note=None):
    return _transition(task_id, "queued", note or "блокер снят")


# ---------------------------------------------------------------- очередь
def next_task(agent=None):
    """Раздел 23: очередь всегда отдаёт то, что БЛИЖЕ К ДЕНЬГАМ."""
    c = _con()
    q = ("SELECT id,objective,next_action,owner_agent,money_proximity,attempts FROM tasks "
         "WHERE state IN ('queued','failed')")
    args = ()
    if agent:
        q += " AND owner_agent=?"
        args = (agent,)
    q += " ORDER BY money_proximity ASC, attempts ASC, id ASC LIMIT 1"
    r = c.execute(q, args).fetchone()
    c.close()
    if not r:
        return None
    return {"id": r[0], "objective": r[1], "next_action": r[2],
            "owner_agent": r[3], "money_proximity": r[4], "attempts": r[5]}


def board():
    """Состояние доски задач."""
    c = _con()
    out = {s: c.execute("SELECT COUNT(*) FROM tasks WHERE state=?", (s,)).fetchone()[0]
           for s in STATES}
    out["with_proof"] = c.execute("SELECT COUNT(DISTINCT task_id) FROM proof_of_work").fetchone()[0]
    out["blocked_detail"] = [dict(zip(("id", "kind", "detail"), r)) for r in c.execute(
        "SELECT id, blocker_kind, blocker_detail FROM tasks WHERE state='blocked'")]
    out["next"] = None
    c.close()
    n = next_task()
    out["next"] = f"#{n['id']} {n['next_action'][:70]}" if n else None
    return out


def stalled(hours=6):
    """Раздел 43: что висит и требует эскалации."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    c = _con()
    # QUEUED ТОЖЕ ЗАВИСАЕТ. Прежний список состояний включал только начатые и
    # упавшие задачи, поэтому шесть задач, ближайших к деньгам, простояли
    # нетронутыми двадцать шесть часов — а уборка честно докладывала «зависших
    # нет». Задача, которую никто не начал, застревает не менее надёжно, чем
    # начатая и брошенная; разница лишь в том, что первую не видно.
    rows = c.execute("SELECT id,objective,state,attempts,updated_at FROM tasks "
                     "WHERE state IN ('running','failed','queued') "
                     "AND updated_at < ?", (cutoff,)).fetchall()
    c.close()
    return [dict(zip(("id", "objective", "state", "attempts", "updated_at"), r)) for r in rows]
