"""ПОЧТАЛЬОН — агент, управляющий почтовым каналом.

ЧЕСТНОЕ ОГРАНИЧЕНИЕ, установленное проверкой API (а не предположением):
  POST /campaigns  -> 405 Method Not Allowed
  SMTP             -> хоста нет, эндпоинтов в API нет
Бесплатный тариф EmailOctopus НЕ ДАЁТ программной отправки. Ни через API,
ни через SMTP. Агент, который «шлёт письма», технически невозможен.

Что возможно и что здесь реализовано:
  1. Владелец ОДИН РАЗ собирает автоматизации в панели (3 бесплатные, 5 шагов)
  2. Каждая автоматизация запускается по ТЕГУ
  3. Агент управляет тегами — и этим запускает отправку

То есть отправляет платформа, а решает КОГДА и КОМУ — агент. Разделение честное:
мы не притворяемся, что умеем то, чего не умеем.

  sync()      — забрать подписчиков из EmailOctopus в нашу базу (наш список = наш актив)
  advance()   — продвинуть подписчиков по серии, переставляя теги
  stats()     — состояние воронки
  setup_todo()— что владельцу нужно собрать руками, по шагам
"""
import sys
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.db import connect
from core import guard
from core.eo import call

LIST_ID = "5aafce28-ad18-11f1-9ced-1760a9b2e09e"
SEND_CAP_PER_DAY = 200          # предел в КОДЕ, а не в промпте
SEQ_TAGS = ["seq:1", "seq:2", "seq:3", "seq:4", "seq:5"]


def now():
    return datetime.now(timezone.utc).isoformat()


def say(text):
    con = connect()
    dup = con.execute("SELECT 1 FROM messages WHERE topic='chat' AND body=? "
                      "LIMIT 1", (text,)).fetchone()   # без окна: см. core/bus.broadcast
    if not dup:
        con.execute("INSERT INTO messages(sender,recipient,topic,body,created_at) "
                    "VALUES (?,?,?,?,?)", ("postman", None, "chat", text, now()))
        con.commit()
    con.close()


# ---------------------------------------------------------------- 1. синхронизация
def sync():
    """Забираем подписчиков к себе. Список — наш актив, ESP только труба:
    если аккаунт заблокируют, мы не потеряем людей."""
    guard.check_action("research", "GREEN")
    st, d = call("GET", f"/lists/{LIST_ID}/contacts")
    if st != 200:
        return f"EmailOctopus недоступен: HTTP {st}"
    contacts = d.get("data", [])
    con = connect()
    added = 0
    for c in contacts:
        email = (c.get("email_address") or "").lower()
        if not email:
            continue
        cur = con.execute(
            "INSERT OR IGNORE INTO subscribers(email,esp_id,status,source,created_at) "
            "VALUES (?,?,?,?,?)",
            (email, c.get("id"), c.get("status") or "pending", "emailoctopus", now()))
        added += cur.rowcount
        con.execute("UPDATE subscribers SET status=?, esp_id=? WHERE email=?",
                    (c.get("status") or "pending", c.get("id"), email))
    con.commit()
    total = con.execute("SELECT COUNT(*) FROM subscribers").fetchone()[0]
    con.close()
    if added:
        say(f"Новые подписчики: {added}. Всего в нашей базе {total}. "
            f"Список хранится у нас — бан ESP не отнимет людей.")
    return f"синхронизировано: {len(contacts)} в ESP, {added} новых, {total} у нас"


# ---------------------------------------------------------------- 2. продвижение
def advance(dry_run=True):
    """Продвигает подписчиков по серии, переставляя теги. Отправку выполняет
    автоматизация EmailOctopus, привязанная к тегу."""
    guard.check_action("email_tag", "YELLOW")
    con = connect()
    subs = con.execute(
        "SELECT id,email,esp_id,segment,created_at FROM subscribers "
        "WHERE status IN ('subscribed','confirmed') ORDER BY id").fetchall()
    if not subs:
        con.close()
        say("Подписчиков нет — продвигать некого. Серия из 5 писем готова и ждёт первого человека.")
        return "подписчиков нет"

    seq = {r[0]: r[1] for r in con.execute("SELECT step,delay_days FROM email_sequence")}
    sent_today = con.execute(
        "SELECT COUNT(*) FROM email_events WHERE event='sent' AND date(occurred_at)=date('now')"
    ).fetchone()[0]

    moved = 0
    for sid, email, esp_id, segment, created in subs:
        if sent_today + moved >= SEND_CAP_PER_DAY:
            say(f"Достигнут дневной предел {SEND_CAP_PER_DAY} — останавливаюсь. "
                f"Предел стоит в коде, обойти его нельзя.")
            break
        done = con.execute(
            "SELECT COUNT(*) FROM email_events WHERE subscriber_id=? AND event='sent'",
            (sid,)).fetchone()[0]
        step = done + 1
        if step > len(SEQ_TAGS):
            continue
        try:
            age_days = (datetime.now(timezone.utc)
                        - datetime.fromisoformat(created)).days
        except Exception:
            age_days = 0
        if age_days < seq.get(step, 0):
            continue
        if dry_run:
            moved += 1
            continue
        st, _ = call("PUT", f"/lists/{LIST_ID}/contacts/{esp_id}",
                     {"tags": {SEQ_TAGS[step - 1]: True}})
        if st in (200, 204):
            con.execute("INSERT INTO email_events(subscriber_id,campaign,event,occurred_at) "
                        "VALUES (?,?,?,?)", (sid, SEQ_TAGS[step - 1], "sent", now()))
            moved += 1
    con.commit()
    con.close()
    if moved:
        say(f"{'Готов продвинуть' if dry_run else 'Продвинул'} {moved} подписчиков по серии "
            f"(тег запускает автоматизацию).")
    return f"{'к продвижению' if dry_run else 'продвинуто'}: {moved}"


# ---------------------------------------------------------------- 3. состояние
def stats():
    con = connect()
    q = lambda s: con.execute(s).fetchone()[0]
    out = {
        "subscribers_total": q("SELECT COUNT(*) FROM subscribers"),
        "confirmed": q("SELECT COUNT(*) FROM subscribers WHERE status IN ('subscribed','confirmed')"),
        "pending": q("SELECT COUNT(*) FROM subscribers WHERE status='pending'"),
        "sequence_steps": q("SELECT COUNT(*) FROM email_sequence"),
        "sent_total": q("SELECT COUNT(*) FROM email_events WHERE event='sent'"),
    }
    con.close()
    return out


# ---------------------------------------------------------------- 4. что нужно от владельца
def setup_todo():
    """Единственное, чего агент сделать не может: собрать автоматизации в панели.
    API их не отдаёт (проверено: /automations -> 404)."""
    s = stats()
    return {
        "почему": "Бесплатный тариф не даёт программной отправки: POST /campaigns = 405, SMTP нет. "
                  "Отправлять может только сама платформа по автоматизации.",
        "шаги_владельца": [
            "EmailOctopus -> Automations -> создать автоматизацию",
            "Триггер: добавлен тег seq:1",
            "Действие: отправить письмо №1 (текст лежит в таблице email_sequence)",
            "Повторить для seq:2 и seq:3 (на бесплатном тарифе доступно 3 автоматизации)",
        ],
        "что_делает_агент_дальше": "Сам расставляет теги seq:N по расписанию серии — "
                                   "это и запускает отправку. Дневной предел зашит в код.",
        "готово_к_работе": {"писем в серии": s["sequence_steps"],
                            "подписчиков": s["subscribers_total"],
                            "подтверждённых": s["confirmed"]},
    }


# ВХОЛОСТУЮ БОЛЬШЕ НЕ ХОДИМ. Флаг стоял с тех пор, когда отправка казалась
# опасной; на деле этот шаг лишь переставляет теги ЛЮДЯМ, КОТОРЫЕ САМИ
# ПОДПИСАЛИСЬ, а рассылку выполняет их почтовая служба по своим правилам.
# Опасности в нём нет, а холостой ход создавал видимость работающего канала.
#
# Настоящее препятствие другое, и флаг его не снимает: подписчиков НОЛЬ.
# Рассылать на собранные адреса нельзя по закону и по правилам площадки, а
# значит задача здесь не «отправить», а «довести до первого подписавшегося».
CYCLE = [("mail_sync", sync), ("mail_advance", lambda: advance(dry_run=False))]


if __name__ == "__main__":
    print("=== СИНХРОНИЗАЦИЯ ===")
    print(" ", sync())
    print("\n=== СОСТОЯНИЕ ВОРОНКИ ===")
    for k, v in stats().items():
        print(f"  {k:20} {v}")
    print("\n=== ПРОДВИЖЕНИЕ (проверка вхолостую) ===")
    print(" ", advance(dry_run=True))
    print("\n=== ЧТО НУЖНО ОТ ВЛАДЕЛЬЦА ===")
    t = setup_todo()
    print("  Почему:", t["почему"])
    for i, step in enumerate(t["шаги_владельца"], 1):
        print(f"    {i}. {step}")
