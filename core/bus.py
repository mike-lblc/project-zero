"""ШИНА АГЕНТОВ — прямая связь друг с другом, а не разговор в пустоту.

До этого агенты только вещали в общий чат. Здесь появляется настоящее общение:
  ask()      — агент задаёт вопрос конкретному агенту и ждёт ответ
  answer()   — агент отвечает на конкретный вопрос
  inbox()    — личные сообщения агента
  handoff()  — передача работы другому агенту с контекстом
  broadcast()— объявление всем

Всё пишется в таблицу messages, поэтому любой диалог виден в дашборде и
проверяем задним числом. Агент не может соврать о том, что он «спрашивал».
"""
import sys, json
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.db import connect

# кто на что отвечает - чтобы агенты знали, к кому идти
DIRECTORY = {
    "scout":        "поиск данных, краул рынка, подшивка источников",
    "verifier":     "независимая перепроверка фактов",
    "adversary":    "поиск дыр, попытка убить предложение",
    "judge":        "решение с разбором сильнейшего возражения",
    "proposer":     "оформление предложения с фальсификатором",
    "orchestrator": "маршрутизация, гейты, журнал",
    "explorer":     "свободные ниши и альтернативные пути",
    "critic":       "проверка работы остальных агентов",
    "optimizer":    "метрики системы и улучшения",
    "merchant":     "ценообразование и тарифы",
    "distributor":  "листинг, обнаружимость, каналы",
    "scribe":       "тексты: отчёты, письма, описания",
    "watchdog":     "надзор за живостью агентов и сервиса",
}


def now():
    return datetime.now(timezone.utc).isoformat()


def _put(sender, recipient, topic, body):
    con = connect()
    mid = con.execute(
        "INSERT INTO messages(sender,recipient,topic,body,created_at) VALUES (?,?,?,?,?)",
        (sender, recipient, topic, body, now())).lastrowid
    con.commit(); con.close()
    return mid


def broadcast(sender, text):
    """Объявление всем. Дословный повтор молча пропускается: повторять одно и
    то же — не работа, а шум.

    ОКНО ПО ВРЕМЕНИ УБРАНО. Здесь была вторая дверь для дублей: worker.say()
    уже перестал их пропускать, а broadcast продолжал разрешать повтор спустя
    шесть часов — и в чате снова копились дословные копии, которые уборка
    тут же удаляла. Две части системы работали друг против друга.

    Подтверждение того, что агенты живы, берётся из журнала прогонов, а не из
    повторённой реплики, поэтому окно здесь не нужно вовсе.
    """
    con = connect()
    dup = con.execute("SELECT 1 FROM messages WHERE topic='chat' AND body=? LIMIT 1",
                      (text,)).fetchone()
    con.close()
    if dup:
        return None
    return _put(sender, None, "chat", text)


def ask(sender, recipient, question, context=None):
    """Вопрос КОНКРЕТНОМУ агенту. Возвращает id, по которому придёт ответ."""
    if recipient not in DIRECTORY:
        raise ValueError(f"нет такого агента: {recipient}")
    body = json.dumps({"q": question, "ctx": context or {}}, ensure_ascii=False)
    mid = _put(sender, recipient, "ask", body)
    broadcast(sender, f"→ спрашиваю у «{recipient}»: {question}")
    return mid


def answer(sender, ask_id, response):
    """Ответ на конкретный вопрос. Помечает вопрос обработанным."""
    con = connect()
    row = con.execute("SELECT sender, body FROM messages WHERE id=? AND topic='ask'",
                      (ask_id,)).fetchone()
    if not row:
        con.close(); raise ValueError(f"нет вопроса #{ask_id}")
    con.execute("UPDATE messages SET consumed_at=? WHERE id=?", (now(), ask_id))
    con.commit(); con.close()
    asker = row[0]
    try:
        q = json.loads(row[1]).get("q", "")
    except Exception:
        q = ""
    mid = _put(sender, asker, "answer",
               json.dumps({"ask_id": ask_id, "a": response}, ensure_ascii=False))
    broadcast(sender, f"← отвечаю «{asker}» на «{q[:60]}»: {str(response)[:160]}")
    return mid


def pending_questions(agent):
    """Вопросы, адресованные этому агенту и ещё не отвеченные."""
    con = connect()
    rows = con.execute("SELECT id, sender, body, created_at FROM messages "
                       "WHERE recipient=? AND topic='ask' AND consumed_at IS NULL ORDER BY id",
                       (agent,)).fetchall()
    con.close()
    out = []
    for r in rows:
        try:
            d = json.loads(r[2])
        except Exception:
            d = {"q": r[2], "ctx": {}}
        out.append({"id": r[0], "from": r[1], "question": d.get("q"),
                    "ctx": d.get("ctx"), "at": r[3]})
    return out


def answers_for(agent, limit=20):
    """Ответы, пришедшие этому агенту."""
    con = connect()
    rows = con.execute("SELECT id, sender, body, created_at FROM messages "
                       "WHERE recipient=? AND topic='answer' ORDER BY id DESC LIMIT ?",
                       (agent, limit)).fetchall()
    con.close()
    out = []
    for r in rows:
        try:
            d = json.loads(r[2])
        except Exception:
            d = {"a": r[2]}
        out.append({"id": r[0], "from": r[1], "answer": d.get("a"), "at": r[3]})
    return out


def handoff(sender, recipient, task, why):
    """Передача работы с объяснением ПОЧЕМУ именно этому агенту."""
    if recipient not in DIRECTORY:
        raise ValueError(f"нет такого агента: {recipient}")
    mid = _put(sender, recipient, "handoff",
               json.dumps({"task": task, "why": why}, ensure_ascii=False))
    broadcast(sender, f"⇉ передаю «{recipient}»: {task}. Почему ему: {why}")
    return mid


def my_work(agent):
    """Задачи, переданные этому агенту и ещё не взятые."""
    con = connect()
    rows = con.execute("SELECT id, sender, body FROM messages "
                       "WHERE recipient=? AND topic='handoff' AND consumed_at IS NULL ORDER BY id",
                       (agent,)).fetchall()
    con.close()
    out = []
    for r in rows:
        try:
            d = json.loads(r[2])
        except Exception:
            d = {"task": r[2], "why": ""}
        out.append({"id": r[0], "from": r[1], "task": d.get("task"), "why": d.get("why")})
    return out


def take(agent, msg_id):
    con = connect()
    con.execute("UPDATE messages SET consumed_at=? WHERE id=? AND recipient=?",
                (now(), msg_id, agent))
    con.commit(); con.close()


def stats():
    con = connect()
    q = lambda s: con.execute(s).fetchone()[0]
    out = {
        "broadcast": q("SELECT COUNT(*) FROM messages WHERE topic='chat'"),
        "asked":     q("SELECT COUNT(*) FROM messages WHERE topic='ask'"),
        "answered":  q("SELECT COUNT(*) FROM messages WHERE topic='answer'"),
        "handoffs":  q("SELECT COUNT(*) FROM messages WHERE topic='handoff'"),
        "unanswered": q("SELECT COUNT(*) FROM messages WHERE topic='ask' AND consumed_at IS NULL"),
    }
    con.close()
    return out
