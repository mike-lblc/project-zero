"""ЗАКРЫВАЮЩИЙ, ПРОВЕРЯЮЩИЙ И УПРАВЛЯЮЩИЙ КАНАЛАМИ — три роли, которых не было.

Директива перечисляет роли, обязанные быть в системе. Трёх не существовало
вовсе, и это объясняло разрыв, который мы видели весь день: сообщение уходило,
и на этом всё кончалось.

    closer           ведёт разговор от ответа до договорённости
    verifier         проверяет, что доказательство действительно доказывает
    channel_manager  отвечает за доставку и за её след

ПОЧЕМУ ОНИ В ОДНОМ ФАЙЛЕ. Они работают с одним и тем же — с перепиской и её
следами, — и разносить их по трём модулям значило бы трижды повторить чтение
той же таблицы. Роли при этом остаются раздельными: у каждой свой промпт, свой
набор инструментов и свой KPI.

ГЛАВНОЕ ПРАВИЛО НА ВСЕ ТРИ. Ответ, которого не было, не додумывается. Мы не
пишем «вероятно, они согласны» и не переводим сделку дальше по конвейеру,
пока другая сторона не сказала своего слова следом, который можно предъявить.
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


def now():
    return datetime.now(timezone.utc).isoformat()


def _gh(args, timeout=60):
    try:
        r = subprocess.run(["gh"] + args, capture_output=True, text=True,
                           timeout=timeout, encoding="utf-8", errors="ignore")
    except (subprocess.SubprocessError, OSError):
        return None
    return r.stdout if r.returncode == 0 else None


# ═══════════════════════════════════════════════ ЗАКРЫВАЮЩИЙ
def check_replies():
    """Смотрит, ответил ли кто-нибудь на наши обращения.

    Это и есть работа закрывающего: перевести сделку из «написали» в
    «ответили», но только по настоящему следу. Отсутствие ответа — это
    отсутствие ответа, а не повод написать второй раз: мы пишем один раз
    навсегда, и это правило дороже любой отдельной сделки.
    """
    guard.check_action("research", "GREEN")
    c = connect()
    c.execute("""CREATE TABLE IF NOT EXISTS outreach (
        id INTEGER PRIMARY KEY, domain TEXT NOT NULL UNIQUE, channel TEXT,
        url TEXT, sent_at TEXT NOT NULL, note TEXT)""")
    rows = c.execute("SELECT domain, channel, url FROM outreach "
                     "WHERE url IS NOT NULL").fetchall()
    c.close()
    if not rows:
        return "обращений не было — отвечать некому"

    replied, silent, unreadable = [], [], []
    for domain, channel, url in rows:
        num = (url or "").rstrip("/").split("/")[-1]
        if not num.isdigit() or not channel:
            unreadable.append(domain)
            continue
        raw = _gh(["api", f"repos/{channel}/issues/{num}/comments",
                   "--jq", '[.[] | select(.user.login != "mike-lblc")] | length'])
        if raw is None:
            unreadable.append(domain)      # молчание API — не молчание адресата
            continue
        n = int((raw or "0").strip() or 0)
        (replied if n > 0 else silent).append(f"{domain} ({n})")

    parts = [f"проверено обращений: {len(rows)}"]
    if replied:
        parts.append("ОТВЕТИЛИ: " + ", ".join(replied))
        bus.broadcast("closer", f"Есть ответ по обращениям: {', '.join(replied)}. "
                                f"Это первый настоящий разговор — веду дальше.")
    else:
        parts.append("ответов пока нет")
    if unreadable:
        parts.append(f"НЕ ПРОЧИТАНО (это незнание, а не молчание): {len(unreadable)}")
    return "; ".join(parts)


def open_deals():
    """Что сейчас в работе по конвейеру сделки, по состояниям."""
    from core import execution
    c = connect()
    rows = c.execute("SELECT state, COUNT(*) FROM tasks GROUP BY state "
                     "ORDER BY 2 DESC").fetchall()
    c.close()
    deal_states = {s for s in execution.STATES if s.isupper()}
    mine = {s: n for s, n in rows if s in deal_states}
    if not mine:
        return "сделок в конвейере нет — ни одна возможность не доведена до разговора"
    return "сделки по состояниям: " + ", ".join(f"{k} {v}" for k, v in mine.items())


# ═══════════════════════════════════════════════ ПРОВЕРЯЮЩИЙ
def verify_evidence():
    """Проверяет, что записанные доказательства ДЕЙСТВИТЕЛЬНО доказывают.

    Доказательство, которого никто не перепроверял, — это утверждение с
    красивым именем. Здесь проверяются три вещи, и каждая механическая:
    ссылка открывается, хеш имеет верную форму, файл существует.
    """
    guard.check_action("research", "GREEN")
    c = connect()
    try:
        rows = c.execute("SELECT id, kind, reference FROM task_proofs "
                         "ORDER BY id DESC LIMIT 40").fetchall()
    except Exception:
        rows = []
    c.close()
    if not rows:
        return "доказательств в журнале нет — проверять нечего"

    good, bad = 0, []
    import re
    import urllib.error
    import urllib.request
    for pid, kind, ref in rows:
        ref = str(ref or "")
        if kind == "tx_hash" or re.fullmatch(r"0x[0-9a-fA-F]{64}", ref):
            (good,) = (good + 1,) if re.fullmatch(r"0x[0-9a-fA-F]{64}", ref) else (good,)
            if not re.fullmatch(r"0x[0-9a-fA-F]{64}", ref):
                bad.append(f"#{pid}: хеш неверной формы")
            continue
        if ref.startswith("http"):
            try:
                urllib.request.urlopen(urllib.request.Request(
                    ref, headers={"User-Agent": "P0-verifier/1.0"}), timeout=15)
                good += 1
            except urllib.error.HTTPError as e:
                if e.code in (401, 403):
                    good += 1          # закрыто, но существует
                else:
                    bad.append(f"#{pid}: ссылка отвечает {e.code}")
            except Exception:
                bad.append(f"#{pid}: ссылка недостижима")
            continue
        if (ROOT / ref).exists():
            good += 1
            continue
        bad.append(f"#{pid}: не ссылка, не хеш, не файл")

    out = f"проверено доказательств {len(rows)}: подтвердилось {good}"
    if bad:
        out += "; НЕ ПОДТВЕРДИЛОСЬ: " + "; ".join(bad[:3])
    return out


# ═══════════════════════════════════════════════ УПРАВЛЯЮЩИЙ КАНАЛАМИ
def channel_health():
    """Какие каналы доставки живы и чем это подтверждено.

    Канал, который «есть», и канал, по которому прошло сообщение с следом, —
    разные вещи. Директива требует: если канал не подключён, отмечать это
    внешним блокером, а не тишиной.
    """
    guard.check_action("research", "GREEN")
    c = connect()
    c.execute("""CREATE TABLE IF NOT EXISTS outreach (
        id INTEGER PRIMARY KEY, domain TEXT NOT NULL UNIQUE, channel TEXT,
        url TEXT, sent_at TEXT NOT NULL, note TEXT)""")
    sent = c.execute("SELECT COUNT(*) FROM outreach WHERE url IS NOT NULL").fetchone()[0]
    subs = 0
    try:
        subs = c.execute("SELECT COUNT(*) FROM subscribers "
                         "WHERE status IN ('subscribed','confirmed')").fetchone()[0]
    except Exception:
        pass
    c.close()

    channels = [
        ("GitHub: публичные обсуждения", sent > 0,
         f"доказано {sent} отправками со ссылкой" if sent
         else "ни одной отправки — канал не доказан"),
        ("почта по согласию", subs > 0,
         f"подписчиков {subs}" if subs
         else "ВНЕШНИЙ БЛОКЕР: подписчиков ноль, рассылать на собранные адреса нельзя"),
    ]
    live = [name for name, ok, _ in channels if ok]
    blocked = [f"{name}: {why}" for name, ok, why in channels if not ok]
    out = f"каналов доставки живых {len(live)} из {len(channels)}"
    if live:
        out += " (" + ", ".join(live) + ")"
    if blocked:
        out += "; " + "; ".join(blocked)
    return out


CLOSER_CYCLE = [("check_replies", check_replies), ("open_deals", open_deals)]
VERIFIER_CYCLE = [("verify_evidence", verify_evidence)]
CHANNEL_CYCLE = [("channel_health", channel_health)]


if __name__ == "__main__":
    print("закрывающий:", check_replies())
    print("сделки:     ", open_deals())
    print("проверяющий:", verify_evidence())
    print("каналы:     ", channel_health())
