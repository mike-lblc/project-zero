"""СОБЫТИЯ, КОТОРЫЕ БУДЯТ АГЕНТА — вместо обхода по кругу.

ЧЕМ ЭТО БЫЛО РАНЬШЕ. Цикл крутил тридцать девять шагов по очереди, и каждый
получал слово раз в круг независимо от того, есть ли для него работа. Обратная
сторона тоже плоха: когда работа ПОЯВЛЯЛАСЬ — пришло ревью, обнаружена свежая
премия, упал инвариант — она ждала своей очереди до следующего оборота.

Это не автономность, а расписание. Агент, узнающий о срочном через сорок
минут, не реагирует, а отчитывается задним числом.

ЧТО ЗДЕСЬ. Настоящая событийная шина на том, что уже есть: событие публикуется,
подписчики определяются по типу, и разбудить их можно немедленно. Ставить ради
этого отдельного брокера (NATS, RabbitMQ) значило бы завести второй процесс,
второй источник отказов и второе место, где состояние может разойтись с базой.
При наших объёмах — десятки событий в минуту, а не десятки тысяч — это была бы
сложность ради названия в списке технологий.

ЧЕСТНАЯ ГРАНИЦА. Доставка здесь «не более одного раза с подтверждением»:
событие помечается обработанным только после того, как обработчик отработал.
Если процесс умер посреди обработки, событие останется неподтверждённым и
достанется следующему — это лучше, чем потерять его молча.

ПРИОРИТЕТ — ЧАСТЬ СОБЫТИЯ. Пришедшее ревью и обновлённая медиана цены не
равны по срочности, и очередь, не различающая их, превращается в тот же
обход по кругу, только менее предсказуемый.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.db import connect, ensure_schema  # noqa: E402

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,
  payload TEXT,
  source TEXT,
  priority INTEGER NOT NULL DEFAULT 5,   -- 1 самое срочное, 9 фоновое
  created_at TEXT NOT NULL,
  claimed_by TEXT,
  claimed_at TEXT,
  done_at TEXT,
  result TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_open ON events(done_at, priority, id);
"""

# КТО НА ЧТО ПОДПИСАН. Событие без подписчика — это не событие, а запись в
# журнал: публиковать такое можно, но будить оно никого не будет, и об этом
# лучше знать явно.
SUBSCRIPTIONS = {
    "pr_review_arrived": ("craftsman", 1),      # ревью пришло — самое срочное
    "payout_announced": ("craftsman", 1),       # объявлена выплата
    "payment_received": ("orchestrator", 1),    # деньги на кошельке
    "fresh_bounty": ("bounty", 2),              # свежая премия, толпы ещё нет
    "invariant_broken": ("adversary", 2),       # проверка, выведенная из поломки
    "worker_down": ("watchdog", 2),
    "new_open_path": ("prospector", 3),         # найден открытый путь к деньгам
    "lead_defect_found": ("leads", 3),          # законный повод обратиться
    "escalation_answered": ("craftsman", 3),    # суждение разрешено, можно делать
    "market_changed": ("scout", 6),             # фоновое: данные обновились
}


def now():
    return datetime.now(timezone.utc).isoformat()


def _con():
    c = connect()
    ensure_schema(c, SCHEMA)
    return c


def publish(kind, payload=None, source=None):
    """Публикует событие. Возвращает (id, кому адресовано) или (id, None)."""
    who, prio = SUBSCRIPTIONS.get(kind, (None, 7))
    encoded = json.dumps(payload or {}, ensure_ascii=False)
    if len(encoded.encode("utf-8")) > 65536:
        raise ValueError("Event payload exceeds 64 KiB; store content and send a reference")
    c = _con()
    cur = c.execute("""INSERT INTO events(kind,payload,source,priority,created_at)
                       VALUES (?,?,?,?,?)""",
                    (kind, encoded,
                     source, prio, now()))
    eid = cur.lastrowid
    c.commit(); c.close()
    return eid, who


def pending(agent=None, limit=10):
    """Что ждёт обработки. Срочное впереди фонового."""
    c = _con()
    if agent:
        kinds = [k for k, (who, _) in SUBSCRIPTIONS.items() if who == agent]
        if not kinds:
            c.close()
            return []
        marks = ",".join("?" * len(kinds))
        rows = c.execute(f"""SELECT id,kind,payload,priority,created_at FROM events
                             WHERE done_at IS NULL AND claimed_by IS NULL AND kind IN ({marks})
                             ORDER BY priority, id LIMIT ?""",
                         (*kinds, limit)).fetchall()
    else:
        rows = c.execute("""SELECT id,kind,payload,priority,created_at FROM events
                            WHERE done_at IS NULL AND claimed_by IS NULL ORDER BY priority, id LIMIT ?""",
                         (limit,)).fetchall()
    c.close()
    return [dict(zip(("id", "kind", "payload", "priority", "at"), r)) for r in rows]


def claim(event_id, agent):
    """Берёт событие в работу. Двое не возьмут одно и то же."""
    c = _con()
    n = c.execute("""UPDATE events SET claimed_by=?, claimed_at=?
                     WHERE id=? AND claimed_by IS NULL AND done_at IS NULL""",
                  (agent, now(), event_id)).rowcount
    c.commit(); c.close()
    return n > 0


def complete(event_id, result):
    """Подтверждает обработку. ТОЛЬКО после того, как работа сделана."""
    c = _con()
    c.execute("UPDATE events SET done_at=?, result=? WHERE id=?",
              (now(), str(result)[:400], event_id))
    c.commit(); c.close()


def next_awake():
    """Кого будить прямо сейчас и по какому поводу.

    Это и есть замена обходу по кругу: цикл спрашивает, есть ли срочное, и
    если есть — даёт слово тому, кого оно касается, вне очереди.
    """
    todo = pending(limit=1)
    if not todo:
        return None
    e = todo[0]
    who, _ = SUBSCRIPTIONS.get(e["kind"], (None, 7))
    return {"agent": who, "event": e} if who else None


def stale(minutes=30):
    """События, взятые в работу и брошенные. Потерянное событие — это тишина."""
    c = _con()
    rows = c.execute("""SELECT id,kind,claimed_by,claimed_at FROM events
                        WHERE done_at IS NULL AND claimed_at IS NOT NULL
                          AND claimed_at < strftime('%Y-%m-%dT%H:%M:%S','now', ?)""",
                     (f"-{minutes} minutes",)).fetchall()
    c.close()
    return [dict(zip(("id", "kind", "by", "at"), r)) for r in rows]


def release(event_id):
    """Возвращает брошенное событие в очередь."""
    c = _con()
    c.execute("UPDATE events SET claimed_by=NULL, claimed_at=NULL WHERE id=?",
              (event_id,))
    c.commit(); c.close()


def stats():
    c = _con()
    q = lambda s: c.execute(s).fetchone()[0]
    out = {
        "всего": q("SELECT COUNT(*) FROM events"),
        "ждут": q("SELECT COUNT(*) FROM events WHERE done_at IS NULL"),
        "срочных": q("SELECT COUNT(*) FROM events WHERE done_at IS NULL AND priority<=2"),
        "обработано": q("SELECT COUNT(*) FROM events WHERE done_at IS NOT NULL"),
        "брошено": len(stale()),
    }
    c.close()
    return out


if __name__ == "__main__":
    print("подписки:")
    for k, (who, p) in sorted(SUBSCRIPTIONS.items(), key=lambda kv: kv[1][1]):
        print(f"  [{p}] {k:<22} -> {who}")
    print()
    eid, who = publish("fresh_bounty", {"repo": "проба", "usd": 75}, source="самопроверка")
    print(f"опубликовано событие {eid}, адресовано: {who}")
    print("кого будить:", next_awake())
    print("взято в работу:", claim(eid, who))
    complete(eid, "проверка связи")
    print("состояние:", stats())
