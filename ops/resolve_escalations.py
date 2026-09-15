"""ОТВЕТЫ НА ЭСКАЛАЦИИ — суждения, которые локальной модели решать запрещено.

council.escalate() кладёт вопрос в очередь (recipient='ESCALATION'), escalation_watch
считает, сколько их накопилось, — и на этом всё заканчивалось: читать очередь было
некому. Десять вопросов лежали с утра, агенты ждали, а со стороны это выглядело как
«решать нечего». Здесь очередь получает читателя: ответ модели-судьи (в сеансе
владельца) уходит агенту-автору вопроса как обычный ответ шины, попадает ему на
общую доску («ответы мне») и снимает вопрос с очереди.

    py -3.13 -X utf8 ops/resolve_escalations.py list
    py -3.13 -X utf8 ops/resolve_escalations.py answer <id> "<ответ>"
    py -3.13 -X utf8 ops/resolve_escalations.py answer-many answers.json   # {"<id>": "<ответ>", ...}
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.db import connect  # noqa: E402

JUDGE = "frontier"      # кто отвечает: модель-судья в сеансе владельца, не агент


def now():
    return datetime.now(timezone.utc).isoformat()


def pending():
    c = connect()
    rows = c.execute("SELECT id, sender, body, created_at FROM messages WHERE recipient='ESCALATION' "
                     "AND consumed_at IS NULL ORDER BY id").fetchall()
    c.close()
    out = []
    for r in rows:
        try:
            d = json.loads(r[2])
        except Exception:
            d = {"question": r[2], "context": ""}
        out.append({"id": r[0], "role": r[1], "question": d.get("question"), "context": d.get("context"),
                    "at": r[3]})
    return out


def answer(esc_id: int, text: str) -> int:
    """Ответ уходит автору вопроса; вопрос снимается с очереди. Возвращает id ответа."""
    c = connect()
    row = c.execute("SELECT sender, body FROM messages WHERE id=? AND recipient='ESCALATION'", (esc_id,)).fetchone()
    if not row:
        c.close(); raise ValueError(f"нет эскалации #{esc_id}")
    role = row[0]
    try:
        q = json.loads(row[1]).get("question", "")
    except Exception:
        q = ""
    stamp = now()
    c.execute("UPDATE messages SET consumed_at=? WHERE id=?", (stamp, esc_id))
    mid = c.execute("INSERT INTO messages(sender,recipient,topic,body,created_at) VALUES (?,?,?,?,?)",
                    (JUDGE, role, "answer", json.dumps({"ask_id": esc_id, "a": text}, ensure_ascii=False), stamp)).lastrowid
    # Коротко в общий чат: остальные агенты видят, какое суждение принято и почему.
    c.execute("INSERT INTO messages(sender,recipient,topic,body,created_at) VALUES (?,?,?,?,?)",
              (JUDGE, None, "chat", f"← суждение для «{role}» по «{str(q)[:70]}»: {text[:200]}", stamp))
    c.commit(); c.close()
    return mid


def main(argv):
    if not argv or argv[0] == "list":
        for e in pending():
            print(f"#{e['id']} [{e['role']} {e['at'][:16]}] {str(e['question'])[:300]}")
            if e["context"]:
                print(f"    ctx: {str(e['context'])[:300]}")
        return 0
    if argv[0] == "answer" and len(argv) >= 3:
        mid = answer(int(argv[1]), " ".join(argv[2:]))
        print(f"ответ #{mid} отправлен")
        return 0
    if argv[0] == "answer-many" and len(argv) == 2:
        data = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
        for k, v in data.items():
            mid = answer(int(k), v)
            print(f"#{k} → ответ #{mid}")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
