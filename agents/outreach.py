"""ОБРАЩЕНИЕ К ПОКУПАТЕЛЮ — по делу, по одному, и только с тем, чего он не знает.

Владелец потребовал, чтобы агенты нашли покупателя и оповестили его. Требование
верное: до сих пор система находила лидов, ставила диагноз, искала канал — и
останавливалась. Двадцать пять компаний, двадцать диагнозов, ноль писем.

ПОЧЕМУ ЭТО НЕ СПАМ, И ГДЕ ПРОХОДИТ ГРАНИЦА. Письмо «у нас есть данные, купите»
— спам, и рассылать его нельзя ни при каких обстоятельствах: оно не несёт
получателю ничего, чего он не мог бы придумать сам. Сообщение «ваши 547 служб
сделали 5 831 вызов за тридцать дней от 701 плательщика, это N-е место среди
14 231 проиндексированной службы» — сведения О НЁМ, измеренные нами и ему
недоступные. Разница не в вежливости формулировки, а в том, есть ли внутри
факт, которого адресат не знал.

ПРАВИЛА, КОТОРЫЕ НЕЛЬЗЯ ОБОЙТИ:
  * одному адресату — ОДИН раз. Навсегда. Повтор превращает сообщение в спам
    задним числом, даже если первое было уместным;
  * не больше одного обращения в сутки на всю систему. Мы ищем покупателя, а
    не рассылаем;
  * только туда, где у адресата есть ПУБЛИЧНЫЙ канал для обсуждения — его
    собственный репозиторий. В личную почту, собранную из открытых источников,
    не пишем: это нарушение закона и правил площадок;
  * внутри обязателен измеренный факт о получателе. Нет факта — нет письма.

ЧЕГО ЗДЕСЬ НЕТ. Ни рассылок, ни списков, ни шаблонов «здравствуйте, меня
зовут». Каждое сообщение собирается из чисел конкретного адресата, и если
чисел нет, оно не отправляется вовсе.
"""
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import guard, bus  # noqa: E402
from core.db import connect  # noqa: E402
from core.identity import SERVICE_URL  # noqa: E402

DAILY_CAP = 1          # обращений в сутки на всю систему


def now():
    return datetime.now(timezone.utc).isoformat()


def _gh(args, timeout=60):
    try:
        r = subprocess.run(["gh"] + args, capture_output=True, text=True,
                           timeout=timeout, encoding="utf-8", errors="ignore")
    except (subprocess.SubprocessError, OSError):
        return None
    return r.stdout if r.returncode == 0 else None


def _schema(c):
    c.execute("""CREATE TABLE IF NOT EXISTS outreach (
        id INTEGER PRIMARY KEY,
        domain TEXT NOT NULL UNIQUE,
        channel TEXT,
        url TEXT,
        sent_at TEXT NOT NULL,
        note TEXT)""")


def rank_of(domain):
    """Место службы среди всех проиндексированных — по числу вызовов.

    Это и есть то, чего адресат не знает: свои цифры он видит, а положение
    среди четырнадцати тысяч — нет. Считается по нашему же каталогу, поэтому
    число проверяемо и им самим.
    """
    catalog = ROOT / "worker" / "catalog.slim.json"
    if not catalog.exists():
        return None
    try:
        rows = json.loads(catalog.read_text(encoding="utf-8"))
    except ValueError:
        return None
    by_host = {}
    for r in rows:
        u = r.get("u") or ""
        if "//" not in u:
            continue
        host = u.split("//", 1)[1].split("/", 1)[0]
        by_host[host] = by_host.get(host, 0) + int(r.get("c") or 0)
    if domain not in by_host:
        return None
    ordered = sorted(by_host.items(), key=lambda kv: -kv[1])
    for i, (host, calls) in enumerate(ordered, 1):
        if host == domain:
            return {"место": i, "всего служб": len(ordered),
                    "вызовов": calls,
                    "выше_нас": [h for h, _ in ordered[max(0, i - 3):i - 1]]}
    return None


def compose(lead, rank):
    """Собирает сообщение из ЕГО чисел. Без чисел сообщения не бывает."""
    domain, services, calls, payers, price = lead
    lines = [
        f"Hi — I run an open index of x402 services and your endpoints came up "
        f"in the measurements. Sharing what I see, in case the comparison is "
        f"useful to you.",
        "",
        f"**{domain}** over the last 30 days:",
        f"- services indexed: **{services}**",
        f"- calls: **{calls:,}**",
        f"- unique payers: **{payers:,}**",
        f"- average price: **${price:.4f}**",
    ]
    if rank:
        lines += [
            "",
            f"That places you **#{rank['место']} by call volume** out of "
            f"**{rank['всего служб']:,}** indexed x402 services.",
        ]
        if rank.get("выше_нас"):
            lines.append(f"Directly above you: {', '.join(rank['выше_нас'])}.")
    lines += [
        "",
        f"The full ranked dataset — every indexed service with 30-day calls, "
        f"unique payers and price bands — is available at {SERVICE_URL}/search "
        f"(x402, $0.01 per query). A free sample is at {SERVICE_URL}/sample if "
        f"you just want to see the shape of it.",
        "",
        "No follow-up from me either way — I only send this once. If the numbers "
        "look wrong, I'd genuinely like to know: the index is only as good as "
        "what it measures.",
    ]
    return "\n".join(lines)


def already_contacted(domain):
    c = connect()
    _schema(c)
    row = c.execute("SELECT sent_at FROM outreach WHERE domain=?", (domain,)).fetchone()
    c.close()
    return row[0] if row else None


def sent_today():
    c = connect()
    _schema(c)
    n = c.execute("SELECT COUNT(*) FROM outreach WHERE sent_at > "
                  "strftime('%Y-%m-%dT%H:%M:%S','now','-1 day')").fetchone()[0]
    c.close()
    return n


def reach_out(dry_run=True):
    """Находит покупателя с публичным каналом и оповещает его. По одному.

    dry_run=True собирает сообщение и НЕ отправляет: так можно посмотреть, что
    именно уйдёт, не отправляя. Это не то же, что «подготовили и забыли» —
    подготовленное здесь возвращается целиком и видно.
    """
    guard.check_action("outreach", "YELLOW")
    if sent_today() >= DAILY_CAP:
        return f"на сегодня предел ({DAILY_CAP}) исчерпан — ищем покупателя, а не рассылаем"

    c = connect()
    _schema(c)
    rows = c.execute(
        """SELECT domain, services, calls_30d, payers_30d, avg_price, channel
           FROM leads
           WHERE channel IS NOT NULL AND channel <> ''
             AND calls_30d > 0 AND payers_30d > 0
             AND domain NOT IN (SELECT domain FROM outreach)
           ORDER BY payers_30d DESC LIMIT 1""").fetchall()
    c.close()
    if not rows:
        return ("покупателей с публичным каналом и измеренными числами не осталось — "
                "всем подходящим уже написано по разу")

    domain, services, calls, payers, price, channel = rows[0]

    # ЯВНЫЙ ЗАСЛОН ПОВЕРХ ЗАПРОСА. Условие «кому ещё не писали» стоит и в
    # выборке, но правило слишком дорогое, чтобы держаться на одном месте:
    # запрос перепишут, условие потеряется, и повтор уйдёт молча. Проверка
    # рядом с отправкой переживает переписывание выборки.
    when = already_contacted(domain)
    if when:
        return f"{domain} уже писали {when[:16]} — второй раз не пишем никогда"

    rank = rank_of(domain)
    body = compose((domain, services, calls, payers, price), rank)
    title = (f"Your x402 numbers for the last 30 days "
             f"({calls:,} calls, {payers:,} payers)")

    if dry_run:
        return {"кому": domain, "канал": channel, "заголовок": title,
                "сообщение": body, "отправлено": False}

    out = _gh(["issue", "create", "--repo", channel, "--title", title,
               "--body", body])
    if not out:
        return {"кому": domain, "отправлено": False,
                "почему": "канал не принял сообщение — возможно, обсуждения закрыты"}

    url = (out or "").strip().splitlines()[-1]
    c = connect()
    _schema(c)
    c.execute("INSERT OR IGNORE INTO outreach(domain,channel,url,sent_at,note) "
              "VALUES (?,?,?,?,?)",
              (domain, channel, url, now(),
               f"место {rank['место']} из {rank['всего служб']}" if rank else "без места"))
    c.commit(); c.close()

    bus.broadcast("salesman", f"Покупателю написано: {domain} через {channel}. "
                              f"В сообщении его собственные цифры: {calls:,} вызовов, "
                              f"{payers:,} плательщиков. Повтора не будет — одному "
                              f"адресату пишем один раз навсегда.")
    return {"кому": domain, "канал": channel, "ссылка": url, "отправлено": True}


CYCLE = [("reach_out", lambda: reach_out(dry_run=False))]


if __name__ == "__main__":
    dry = "--send" not in sys.argv
    r = reach_out(dry_run=dry)
    print(json.dumps(r, ensure_ascii=False, indent=1) if isinstance(r, dict) else r)
