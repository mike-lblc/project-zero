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
    cols = [r[1] for r in c.execute("PRAGMA table_info(outreach)")]
    tid_col = "task_id" if "task_id" in cols else "NULL"
    rows = c.execute(f"SELECT domain, channel, url, sent_at, {tid_col} FROM outreach "
                     "WHERE url IS NOT NULL").fetchall()
    c.close()
    if not rows:
        return "обращений не было — отвечать некому"

    replied, silent, unreadable, awaiting = [], [], [], []
    for domain, channel, url, sent_at, task_id in rows:
        # Номер обсуждения — до якоря комментария: .../pull/13455#issuecomment-…
        num = (url or "").split("#")[0].rstrip("/").split("/")[-1]
        if not num.isdigit() or not channel:
            unreadable.append(domain)
            continue
        # ОТВЕТ — ЭТО ТО, ЧТО НАПИСАНО ПОСЛЕ НАШЕГО СООБЩЕНИЯ. Прежде считались
        # все чужие комментарии обсуждения, и в PR с давним ревью закрывающий
        # объявил бы «ответили» ещё до того, как адресат увидел вопрос.
        since = (sent_at or "")[:19]
        raw = _gh(["api", f"repos/{channel}/issues/{num}/comments?per_page=100",
                   "--jq", f'[.[] | select(.created_at > "{since}") '
                           f'| {{id: .id, user: .user.login, at: .created_at, body: .body}}]'])
        if raw is None:
            unreadable.append(domain)      # молчание API — не молчание адресата
            continue
        try:
            comments = json.loads(raw or "[]")
        except ValueError:
            comments = None
        if not isinstance(comments, list):
            unreadable.append(domain)      # не список комментариев — это незнание, не молчание
            continue
        theirs = [x for x in comments if x.get("user") != OUR_LOGIN]
        n = len(theirs)
        # ЗАКРЫТО МОЛЧА. Обсуждение, закрытое другой стороной без единого слова (blockrun 15.09),
        # — это ответ «нет», а не ожидание. Без этой проверки сделка висела «ждём ответа» сутками.
        if not theirs and task_id:
            st = (_gh(["api", f"repos/{channel}/issues/{num}", "--jq", ".state"]) or "").strip()
            if st == "closed":
                try:
                    from core import execution
                    execution.advance(task_id, "REJECTED", f"обсуждение закрыто другой стороной без ответа: {url}")
                    bus.broadcast("closer", f"{domain}: обсуждение закрыто без ответа — сделка #{task_id} закрыта.")
                except Exception:
                    pass
                silent.append(f"{domain} (закрыто)")
                continue
        (replied if n > 0 else silent).append(f"{domain} ({n})")
        if not theirs:
            continue
        if task_id:
            try:
                from core import execution
                execution.advance(task_id, "REPLIED",
                                  f"ответ другой стороны после нашего сообщения: {url} "
                                  f"(новых комментариев {n})")
            except Exception as e:
                bus.broadcast("closer", f"Ответ есть, но сделка #{task_id} не продвинута: {e}")
        # ЧЕЙ ХОД. Раньше закрывающий лишь СЧИТАЛ ответы: сделка вставала в REPLIED,
        # шёл broadcast «веду дальше» — и никто не вёл: ответ мейнтейнера agent402
        # пролежал 17 часов без реакции. Теперь, если последнее слово не наше,
        # ПОЛНЫЙ текст разговора уходит в очередь суждений (ответ лиду — суждение,
        # локальная модель его не пишет), один раз на каждый их комментарий.
        last = comments[-1]
        c = connect()
        _replies_schema(c)
        if last.get("user") == OUR_LOGIN:
            c.execute("UPDATE outreach_replies SET answered_at=? WHERE url=? AND answered_at IS NULL",
                      (now(), url))
            c.commit(); c.close()
            continue
        known = c.execute("SELECT answered_at FROM outreach_replies WHERE comment_id=?",
                          (last["id"],)).fetchone()
        if known:
            c.close()
            if not known[0]:                 # разобран answer_replies — больше не «ждёт нас»
                awaiting.append(domain)
            continue
        c.execute("INSERT OR IGNORE INTO outreach_replies(comment_id,domain,url,author,created_at,"
                  "body,escalated_at) VALUES (?,?,?,?,?,?,?)",
                  (last["id"], domain, url, last.get("user") or "", last.get("at") or "",
                   str(last.get("body") or "")[:8000], now()))
        c.commit(); c.close()
        ours = [str(x.get("body") or "")[:1500] for x in comments if x.get("user") == OUR_LOGIN][-2:]
        try:
            from agents import council
            council.escalate("closer",
                             f"Лид {domain} ответил в {url} — нужен ответ по существу сегодня "
                             f"(сделка #{task_id})",
                             json.dumps({"url": url, "task_id": task_id,
                                         "их ответ": str(last.get("body") or "")[:4000],
                                         "наши предыдущие": ours}, ensure_ascii=False))
        except Exception as e:
            bus.broadcast("closer", f"Ответ {domain} не удалось поставить в очередь суждений: {e}")
        try:
            from core import events
            events.publish("lead_replied", {"кому": domain, "ссылка": url,
                                            "comment_id": last["id"]}, source="closer")
        except Exception:
            pass
        awaiting.append(domain)

    parts = [f"проверено обращений: {len(rows)}"]
    if replied:
        parts.append("ОТВЕТИЛИ: " + ", ".join(replied))
        bus.broadcast("closer", f"Есть ответ по обращениям: {', '.join(replied)}. "
                                f"Разговор передан в очередь суждений с полным текстом.")
    else:
        parts.append("ответов пока нет")
    if awaiting:
        parts.append(f"ЖДУТ НАШЕГО ОТВЕТА: {', '.join(awaiting)}")
    if unreadable:
        parts.append(f"НЕ ПРОЧИТАНО (это незнание, а не молчание): {len(unreadable)}")
    return "; ".join(parts)


OUR_LOGIN = "mike-lblc"
SERVICE_URL = "https://x402-bazaar-rank.x402-bazaar-rank-worker.workers.dev"
REAL_ASSOC = {"OWNER", "MEMBER", "COLLABORATOR"}       # кто в репозитории действительно решает


def _classify_reply(text):
    """Намерение ответа лида — механическая классификация локальной моделью.
    Возвращает одно из: interested, question, declined, hostile, other."""
    from core import router
    prompt = ("Classify the intent of this reply to our unsolicited offer. Answer ONLY with one word from: "
              "interested, question, declined, hostile, other.\n\nReply:\n" + (text or "")[:2000])
    try:
        raw = (router.run("classify", prompt) or "").strip().lower()
    except Exception:
        return "other"
    for k in ("interested", "question", "declined", "hostile"):
        if k in raw:
            return k
    return "other"


def _reply_text(domain, intent):
    """Ответ по шаблону из фактов — без обещаний и выдумок. Локальная модель текст не пишет."""
    brand = domain.split(".")[0]
    base = (f"Thanks for replying. Concretely, here is what I can give you and what it costs:\n\n"
            f"- A ranked read of your category — every competing x402 service with 30-day calls, "
            f"unique paying wallets and price, with a receipt naming the data snapshot: "
            f"`{SERVICE_URL}/search?q={brand}` — $0.01 per query, settled by x402 (USDC on Base).\n"
            f"- No x402 client? Send $0.01 or more in USDC/USDT/DAI on Base to the address at "
            f"{SERVICE_URL}/pay and open the same URL with `?tx=<hash>`; BTC, ETH, SOL and TRX are "
            f"accepted too (addresses at {SERVICE_URL}/).\n"
            f"- Check first for free: {SERVICE_URL}/sample returns three ranked results with the same "
            f"receipt, {SERVICE_URL}/health shows the current snapshot hash.\n\n")
    if intent == "question":
        base += ("If your question is about how a number was measured, the receipt fields (snapshot hash, "
                 "30-day window, scoring revision) are the answer; if it is about something else, I will "
                 "answer it in this thread within the day.")
    else:
        base += "If you want the comparison narrowed to a specific capability or network, name it here and I will post the exact query."
    return base


def answer_replies(limit=3):
    """ОТВЕТ ЛИДУ БЕЗ ОЖИДАНИЯ СЕАНСА (владелец 16.09: «it shouldn't be delayed»).

    Раньше каждый ответ лида уходил в очередь суждений и ждал сеанса владельца — измерено 17 ч 37 мин
    на agent402. Теперь закрывающий действует сам: закрытое обсуждение → сделка закрыта; комментарий
    не от участника репозитория → шум; отказ → закрыто; интерес или вопрос → ответ по шаблону из
    фактов публикуется сразу, сделка → NEGOTIATING. Очередь суждений по-прежнему получает копию —
    сильная модель может дополнить, но никто её не ждёт.
    """
    guard.check_action("research", "GREEN")
    c = connect()
    _replies_schema(c)
    pending = c.execute("SELECT comment_id, domain, url, author, body FROM outreach_replies "
                        "WHERE answered_at IS NULL ORDER BY created_at LIMIT ?", (limit,)).fetchall()
    tid_of = dict(c.execute("SELECT domain, task_id FROM outreach WHERE task_id IS NOT NULL").fetchall())
    c.close()
    if not pending:
        return "ответов лидов без нашей реакции нет"
    from core import execution
    out = []
    for comment_id, domain, url, author, body in pending:
        repo = "/".join(url.split("github.com/")[-1].split("/")[:2]) if "github.com/" in url else ""
        num = url.split("#")[0].rstrip("/").split("/")[-1]
        if not repo or not num.isdigit():
            out.append(f"{domain}: ссылка не разобрана")
            continue
        raw = _gh(["api", f"repos/{repo}/issues/{num}", "--jq", "{state: .state, pr: (.pull_request != null)}"])
        try:
            issue = json.loads(raw or "{}")
        except ValueError:
            issue = {}
        craw = _gh(["api", f"repos/{repo}/issues/{num}/comments?per_page=100",
                    "--jq", f"[.[] | select(.id == {int(comment_id)})][0] | {{assoc: .author_association, user: .user.login}}"])
        try:
            cinfo = json.loads(craw or "{}") or {}
        except ValueError:
            cinfo = {}
        assoc = str(cinfo.get("assoc") or "").upper()
        tid = tid_of.get(domain)
        decision, posted = None, False
        if issue.get("state") == "closed" and not issue.get("pr"):
            decision = "обсуждение закрыто — сделка закрыта, повторно не пишем"
            if tid:
                try: execution.advance(tid, "REJECTED", f"issue закрыт другой стороной: {url}")
                except Exception: pass
        elif assoc and assoc not in REAL_ASSOC:
            decision = f"комментарий от {author} ({assoc}) — не участник репозитория, шум"
        else:
            intent = _classify_reply(body)
            if intent in ("declined", "hostile"):
                decision = f"ответ «{intent}» — сделка закрыта без реплики"
                if tid:
                    try: execution.advance(tid, "REJECTED", f"лид отказался: {url}")
                    except Exception: pass
            else:
                guard.check_action("lead_reply", "YELLOW")
                text = _reply_text(domain, intent)
                res = _gh(["api", "-X", "POST", f"repos/{repo}/issues/{num}/comments", "-f", f"body={text}", "--jq", ".id"])
                posted = bool((res or "").strip())
                decision = f"ответ «{intent}» — {'реплика опубликована' if posted else 'публикация не удалась'}"
                if posted and tid:
                    try: execution.advance(tid, "NEGOTIATING", f"наш ответ с предложением и ценой: {url}")
                    except Exception: pass
        c = connect()
        c.execute("UPDATE outreach_replies SET answered_at=? WHERE comment_id=?", (now(), comment_id))
        c.commit(); c.close()
        bus.broadcast("closer", f"{domain}: {decision}.")
        out.append(f"{domain}: {decision}")
    return "; ".join(out)


def _replies_schema(c):
    c.execute("""CREATE TABLE IF NOT EXISTS outreach_replies (
        comment_id INTEGER PRIMARY KEY, domain TEXT NOT NULL, url TEXT NOT NULL,
        author TEXT NOT NULL, created_at TEXT NOT NULL, body TEXT NOT NULL,
        escalated_at TEXT, answered_at TEXT)""")


def unanswered_replies():
    """Ответы лидов, на которые мы ещё не ответили, — для дашборда и аудита."""
    c = connect()
    _replies_schema(c)
    rows = [dict(r) for r in c.execute(
        "SELECT domain, url, author, created_at, substr(body,1,300) AS body, escalated_at "
        "FROM outreach_replies WHERE answered_at IS NULL ORDER BY created_at")]
    c.close()
    return rows


def open_deals():
    """Что сейчас в работе по конвейеру сделки, по состояниям."""
    from core import execution
    c = connect()
    execution._con().close()                  # перенос старых имён, если не был
    rows = c.execute("SELECT state, COUNT(*) FROM tasks WHERE kind='deal' GROUP BY state "
                     "ORDER BY 2 DESC").fetchall()
    c.close()
    mine = {s: n for s, n in rows}
    if not mine:
        return "сделок в конвейере нет — ни одна возможность не доведена до разговора"
    return "сделки по состояниям: " + ", ".join(f"{k} {v}" for k, v in mine.items())


# ═══════════════════════════════════════════════ ПРОВЕРЯЮЩИЙ
def verify_evidence():
    """Проверяет, что записанные доказательства ДЕЙСТВИТЕЛЬНО доказывают.

    Доказательство, которого никто не перепроверял, — это утверждение с
    красивым именем. Проверки механические: ссылка открывается, хеш имеет
    верную форму, файл существует.

    ЧТО БЫЛО. Функция читала таблицу task_proofs, которой не существует, ловила
    ошибку широким except и докладывала «доказательств в журнале нет —
    проверять нечего». Проверяющий не проверил ни одного доказательства ни
    разу, а выглядел исправным. Теперь читаются настоящие источники:
    proof_of_work и ссылки-доказательства переходов сделок.
    """
    import re
    import urllib.error
    import urllib.request
    guard.check_action("research", "GREEN")
    from core import execution
    execution._con().close()
    c = connect()
    items = [(f"работа #{t}", k, str(r or "")) for t, k, r in c.execute(
        "SELECT task_id, kind, reference FROM proof_of_work ORDER BY id DESC LIMIT 40")]
    for tid, st, note in c.execute(
            "SELECT e.task_id, e.to_state, e.note FROM task_events e JOIN tasks t ON t.id=e.task_id "
            "WHERE t.kind='deal' AND e.note LIKE '%http%' ORDER BY e.id DESC LIMIT 40"):
        for url in re.findall(r"https?://[^\s)]+", note or ""):
            items.append((f"сделка #{tid} {st}", "http", url))
    c.close()
    if not items:
        return "доказательств в журнале нет — проверять нечего"

    good, bad, unchecked = 0, [], 0
    seen = set()
    for label, kind, ref in items:
        if ref in seen:
            continue
        seen.add(ref)
        if ref.startswith("http"):
            try:
                urllib.request.urlopen(urllib.request.Request(
                    ref, headers={"User-Agent": "P0-verifier/1.0"}), timeout=15)
                good += 1
            except urllib.error.HTTPError as e:
                if e.code in (401, 403, 429):
                    good += 1          # закрыто или ограничено, но существует
                else:
                    bad.append(f"{label}: ссылка отвечает {e.code}")
            except Exception:
                bad.append(f"{label}: ссылка недостижима")
            continue
        if ref.startswith("0x"):
            if re.fullmatch(r"0x[0-9a-fA-F]{64}", ref):
                good += 1
            else:
                bad.append(f"{label}: хеш неверной формы")
            continue
        if kind == "file" or "/" in ref or ref.endswith((".md", ".txt", ".json")):
            if (ROOT / ref).exists():
                good += 1
            else:
                bad.append(f"{label}: не ссылка, не хеш, не файл")
            continue
        unchecked += 1                 # измерение или строка базы: механически не проверить

    out = f"проверено доказательств {len(seen)}: подтвердилось {good}"
    if unchecked:
        out += f"; механически не проверяется {unchecked}"
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

    # Moltbook: канал доказан только опубликованной записью, а не отправленной.
    try:
        from core import moltbook
        blocked = moltbook.writes_blocked()
        mc = connect()
        published = mc.execute("SELECT COUNT(*) FROM moltbook_receipts WHERE action IN "
                               "('post','comment','reply') AND state='CONFIRMED'").fetchone()[0]
        mc.close()
        molt = ("Moltbook", published > 0 and not blocked,
                f"опубликовано записей {published}" if published and not blocked
                else "ВНЕШНИЙ БЛОКЕР: " + (blocked or "ни одной опубликованной записи"))
    except Exception as e:
        molt = ("Moltbook", False, f"состояние не прочитано: {type(e).__name__}")
    channels = [
        molt,
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
