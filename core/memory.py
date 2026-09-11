"""ПАМЯТЬ ЦИКЛА — чтобы агенты не повторялись, а двигались дальше.

Обнаружено владельцем и подтверждено данными: 78% утверждений и 89% реплик были
дословными повторами. Агент крутил один и тот же вывод каждый цикл.

Здесь появляется различие между «я это уже говорил» и «это новое»:
  changed()      — изменился ли результат шага с прошлого раза
  remember()     — запомнить результат шага
  seen_claim()   — было ли уже такое утверждение
  next_focus()   — если нового нет, ЧТО копать дальше (а не повторять)
"""
import sys, hashlib
from pathlib import Path
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.db import connect, ensure_schema

SCHEMA = """
CREATE TABLE IF NOT EXISTS cycle_memory (
  id INTEGER PRIMARY KEY,
  agent TEXT NOT NULL,
  step TEXT NOT NULL,
  digest TEXT NOT NULL,
  value TEXT,
  seen_count INTEGER NOT NULL DEFAULT 1,
  first_at TEXT NOT NULL,
  last_at TEXT NOT NULL,
  skip_until TEXT,
  UNIQUE(agent, step)
);
CREATE TABLE IF NOT EXISTS focus_queue (
  id INTEGER PRIMARY KEY,
  agent TEXT NOT NULL,
  topic TEXT NOT NULL,
  done INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
"""


def now():
    return datetime.now(timezone.utc).isoformat()


def _init(con):
    ensure_schema(con, SCHEMA)


def _digest(value):
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:20]


def changed(agent, step, value):
    """True, если результат шага отличается от прошлого раза.

    Возвращает (изменилось, сколько_раз_подряд_одно_и_то_же).
    Одинаковый результат — не повод молчать вечно: раз в 20 циклов
    агент всё же отчитывается, чтобы «тишина» не путалась со «сломался».
    """
    con = connect()
    _init(con)
    d = _digest(value)
    row = con.execute("SELECT digest, seen_count FROM cycle_memory WHERE agent=? AND step=?",
                      (agent, step)).fetchone()
    if row is None:
        con.execute("INSERT INTO cycle_memory(agent,step,digest,value,first_at,last_at) "
                    "VALUES (?,?,?,?,?,?)", (agent, step, d, str(value)[:800], now(), now()))
        con.commit(); con.close()
        return True, 1
    if row[0] != d:
        con.execute("UPDATE cycle_memory SET digest=?, value=?, seen_count=1, last_at=? "
                    "WHERE agent=? AND step=?", (d, str(value)[:800], now(), agent, step))
        con.commit(); con.close()
        return True, 1
    n = row[1] + 1
    con.execute("UPDATE cycle_memory SET seen_count=?, last_at=? WHERE agent=? AND step=?",
                (n, now(), agent, step))
    con.commit(); con.close()
    return (n % 20 == 0), n          # раз в 20 повторов — короткое подтверждение жизни



def pause(agent, step, minutes):
    """Отправляет шаг на паузу ДО указанного времени. Пауза живёт в базе.

    Так было не сразу, и разница принципиальная. Счёт повторов хранился в базе
    и переживал перезапуск, а решение «пропускать до такого-то оборота» лежало
    в словаре процесса — и умирало вместе с ним. Сторож перезапускал воркера,
    номера оборотов начинались с нуля, пауза испарялась, и шаг снова бежал.
    По журналу это выглядело как восемнадцать одинаковых прогонов подряд при
    формально исправной защите.

    Отсюда два решения: паузa хранится В БАЗЕ и измеряется ЧАСАМИ, а не
    номерами оборотов. Номер оборота — счётчик внутри запуска; привязывать к
    нему то, что должно пережить запуск, бессмысленно по определению.
    """
    until = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    con = connect()
    _init(con)
    con.execute("UPDATE cycle_memory SET skip_until=? WHERE agent=? AND step=?",
                (until.isoformat(), agent, step))
    con.commit(); con.close()
    return until


def paused_until(agent, step):
    """До какого момента шаг на паузе, или None. Читает базу, а не память."""
    con = connect()
    _init(con)
    row = con.execute("SELECT skip_until FROM cycle_memory WHERE agent=? AND step=?",
                      (agent, step)).fetchone()
    con.close()
    if not row or not row[0]:
        return None
    try:
        until = datetime.fromisoformat(row[0])
    except ValueError:
        return None
    return until if until > datetime.now(timezone.utc) else None


def resume(agent, step):
    """Снимает паузу: шаг снова приносит новое."""
    con = connect()
    _init(con)
    con.execute("UPDATE cycle_memory SET skip_until=NULL WHERE agent=? AND step=?",
                (agent, step))
    con.commit(); con.close()


def seen_claim(claim):
    """Было ли уже дословно такое утверждение. Защищает таблицу от мусора."""
    con = connect()
    row = con.execute("SELECT 1 FROM evidence WHERE claim=? LIMIT 1", (claim,)).fetchone()
    con.close()
    return row is not None


def next_focus(agent):
    """Что копать дальше, если по основному направлению нового нет."""
    con = connect()
    _init(con)
    row = con.execute("SELECT id, topic FROM focus_queue WHERE agent=? AND done=0 ORDER BY id",
                      (agent,)).fetchone()
    if not row:
        con.close()
        return None
    con.execute("UPDATE focus_queue SET done=1 WHERE id=?", (row[0],))
    con.commit(); con.close()
    return row[1]


def stats():
    con = connect()
    _init(con)
    q = lambda s: con.execute(s).fetchone()[0]
    out = {
        "steps_tracked": q("SELECT COUNT(*) FROM cycle_memory"),
        "stuck_steps": q("SELECT COUNT(*) FROM cycle_memory WHERE seen_count > 5"),
        "focus_pending": q("SELECT COUNT(*) FROM focus_queue WHERE done=0"),
        "evidence_total": q("SELECT COUNT(*) FROM evidence"),
        "evidence_unique": q("SELECT COUNT(DISTINCT claim) FROM evidence"),
    }
    con.close()
    return out


def cleanup_duplicates():
    """Уборка повторов. СНАЧАЛА считает, есть ли что убирать.

    Раньше удаление шло по всей таблице при каждом вызове, даже когда дублей
    ноль. Такое удаление берёт исключительную блокировку, и на живой системе,
    где воркер пишет каждые несколько секунд, оно падало с «database is locked».
    То есть уборка не просто была бесполезной — она роняла шаг, который её звал.
    """
    con = connect()
    before_e = con.execute("SELECT COUNT(*) FROM evidence").fetchone()[0]
    uniq_e = con.execute("SELECT COUNT(DISTINCT claim) FROM evidence").fetchone()[0]
    before_m = con.execute("SELECT COUNT(*) FROM messages WHERE topic='chat'").fetchone()[0]
    uniq_m = con.execute("SELECT COUNT(DISTINCT body) FROM messages "
                         "WHERE topic='chat'").fetchone()[0]
    if before_e == uniq_e and before_m == uniq_m:
        con.close()
        return {"evidence": (before_e, before_e), "chat": (before_m, before_m),
                "removed": 0}

    if before_e > uniq_e:
        con.execute("""DELETE FROM evidence WHERE id NOT IN
                       (SELECT MIN(id) FROM evidence GROUP BY claim)""")
    if before_m > uniq_m:
        con.execute("""DELETE FROM messages WHERE topic='chat' AND id NOT IN
                       (SELECT MIN(id) FROM messages WHERE topic='chat' GROUP BY body)""")
    con.commit()
    con.close()
    return {"evidence": (before_e, uniq_e), "chat": (before_m, uniq_m),
            "removed": (before_e - uniq_e) + (before_m - uniq_m)}
