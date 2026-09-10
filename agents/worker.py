"""CONTINUOUS WORKER — agents never idle.

Cycle order is deliberate: the mission first (watch for payment), then keep the
product's data fresh, then guard our own reliability, then learn. When there is
no assigned work, agents do self-directed work rather than sit still.

All work here is GREEN (reversible, private, free). Judgment still escalates -
this loop never decides anything, it gathers and measures.
"""
import sys, re, json, time, subprocess, urllib.request, urllib.error
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.db import connect
from core import guard, memory, economics

UA = "Mozilla/5.0 (compatible; P0-worker/0.1)"
ROOT = Path(__file__).resolve().parent.parent
IDX = ROOT / "data" / "bazaar_index.json"
BAZAAR = "https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources"
SERVICE = "http://127.0.0.1:8402"


def now():
    return datetime.now(timezone.utc).isoformat()


def get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode()


def say(agent, text, topic="chat"):
    """Агент говорит ТОЛЬКО если это новое. Повтор одного и того же — не работа.

    Владелец поймал: 89% реплик были дословными дублями. Теперь дубль молча
    пропускается, а редкое подтверждение жизни идёт раз в 20 циклов.
    """
    if topic == "chat":
        con = connect()
        dup = con.execute("SELECT 1 FROM messages WHERE topic='chat' AND body=? "
                          "AND created_at > datetime('now','-6 hours') LIMIT 1", (text,)).fetchone()
        con.close()
        if dup:
            return
    con = connect()
    con.execute("INSERT INTO messages(sender,recipient,topic,body,created_at) VALUES (?,?,?,?,?)",
                (agent, None, topic, text, now()))
    con.commit()
    con.close()


def note(agent, claim, source_id=None, conf=None):
    """След оставляется только для НОВОГО вывода. Дубли не пишутся."""
    if memory.seen_claim(claim):
        return
    con = connect()
    if source_id is None:
        source_id = con.execute(
            "INSERT INTO sources(url,title,fetched_at,raw_excerpt) VALUES (?,?,?,?)",
            (f"worker://{agent}", f"worker cycle {agent}", now(), claim[:400])).lastrowid
    con.execute("INSERT INTO evidence(claim,source_id,agent,created_at,confidence) VALUES (?,?,?,?,?)",
                (claim, source_id, agent, now(), conf))
    con.commit()
    con.close()


AGENT_OF = {"fresh_bounties":"bounty","watch_prs":"craftsman","find_doc_work":"craftsman","hunt_bounties":"bounty","mechanic":"mechanic","find_leads":"leads","diagnose_leads":"salesman","mail_sync":"postman","mail_advance":"postman","economics":"optimizer","briefing":"orchestrator","merchant":"merchant","distributor":"distributor","scribe":"scribe",
            "watchdog":"watchdog","explorer_replies":"explorer",
            "watch_payments":"orchestrator","refresh_market":"scout","scout_research":"scout",
            "health_check":"judge","explore":"explorer","study_market":"verifier",
            "critique":"critic","audit":"adversary","optimize":"optimizer",
            "explore_alternatives":"explorer"}


def record_run(step, agent, ok, detail, started, ended):
    """Каждый шаг цикла попадает в журнал. Без этого 'агенты работают' - слова."""
    con = connect()
    con.execute("INSERT INTO runs(agent,started_at,ended_at,status,notes) VALUES (?,?,?,?,?)",
                (agent, started, ended, "ok" if ok else "error", f"{step}: {detail}"[:400]))
    # репутация считается ТОЛЬКО по исходам, а не по красноречию
    con.execute("""INSERT INTO agent_reputation(agent,role,calls,correct,updated_at)
                   VALUES (?,?,1,?,?)
                   ON CONFLICT(agent,role) DO UPDATE SET
                     calls=calls+1, correct=correct+?, updated_at=?""",
                (agent, step, 1 if ok else 0, now(), 1 if ok else 0, now()))
    con.commit()
    con.close()


def wallet():
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("WALLET_ETH="):
            return line.split("=", 1)[1].strip()
    return None


# ---------------------------------------------------------------- 1. THE MISSION
def watch_payments():
    """Watch the Base address for incoming USDC. This is how the proof is detected.
    Free, no API key: Blockscout public API."""
    addr = wallet()
    if not addr:
        return "no wallet configured"
    say("orchestrator", f"Проверяю кошелёк {addr[:10]}…{addr[-6:]} на Base — не пришёл ли первый платёж. "
                        f"Это единственное, что доказывает миссию.")
    url = f"https://base.blockscout.com/api/v2/addresses/{addr}/token-transfers?type=ERC-20"
    try:
        d = json.loads(get(url, 25))
    except Exception as e:
        return f"payment watch unavailable ({type(e).__name__})"
    items = d.get("items") or []
    con = connect()
    new = 0
    for t in items:
        h = (t.get("transaction_hash") or t.get("tx_hash") or "")
        if not h:
            continue
        to = ((t.get("to") or {}).get("hash") or "").lower()
        if to != addr.lower():
            continue          # only INCOMING counts
        tok = t.get("token") or {}
        dec = int(tok.get("decimals") or 6)
        raw = (t.get("total") or {}).get("value") or t.get("value") or "0"
        try:
            amt = str(int(raw) / (10 ** dec))
        except Exception:
            amt = str(raw)
        try:
            con.execute("INSERT OR IGNORE INTO payments(chain,tx_hash,amount,asset,received_at,attributed_to) "
                        "VALUES (?,?,?,?,?,?)",
                        ("base", h, amt, tok.get("symbol") or "?",
                         t.get("timestamp") or now(), "x402-bazaar-rank"))
            if con.total_changes:
                new += 1
        except Exception:
            pass
    con.commit()
    con.close()
    if new:
        say("orchestrator", f"🎉 ПЛАТЁЖ! Пришло {new} входящих перевода. Если платил не владелец — "
                            f"миссия доказана: агенты заработали с нуля.")
        note("orchestrator", f"*** PAYMENT DETECTED *** {new} incoming transfer(s) to {addr}. "
                             f"MISSION CONDITION MET if from a third party.", conf=1.0)
        return f"NEW PAYMENTS: {new}"
    say("orchestrator", f"Платежей пока нет ({len(items)} переводов в истории). Ждём. "
                        f"Сервис живой, но нас ещё никто не нашёл — узкое место сейчас в этом.")
    return f"no incoming payments yet ({len(items)} transfers seen)"


# ---------------------------------------------------------------- 2. PRODUCT DATA
def refresh_market():
    """Keep the catalog fresh and detect what changed. Change IS the newsletter."""
    say("scout", "Иду за свежим срезом рынка x402 — смотрю, кто появился и у кого растут вызовы.")
    try:
        d = json.loads(get(f"{BAZAAR}?limit=100&offset=0", 30))
    except Exception as e:
        say("scout", f"Рынок недоступен ({type(e).__name__}). Не выдумываю данные — просто пропускаю цикл.")
        return f"bazaar unreachable ({type(e).__name__})"
    items = d.get("items") or []
    total = (d.get("pagination") or {}).get("total")
    if not items:
        return "bazaar returned nothing"
    try:
        old = json.loads(IDX.read_text(encoding="utf-8"))
    except Exception:
        old = []
    known = {i.get("resource") for i in old}
    fresh = [i for i in items if i.get("resource") not in known]
    hot = sorted(items, key=lambda i: -((i.get("quality") or {}).get("l30DaysTotalCalls") or 0))[:3]
    top = ", ".join(f"{(i.get('serviceName') or i.get('resource',''))[:34]}"
                    f"({(i.get('quality') or {}).get('l30DaysTotalCalls',0)})" for i in hot)
    note("scout", f"Market refresh: total={total}, page sampled={len(items)}, "
                  f"new-to-us={len(fresh)}. Top by 30d calls: {top}", conf=0.85)
    say("scout", f"Рынок обновлён: всего {total} сервисов, новых для нас — {len(fresh)}. "
                 f"Лидеры по вызовам за 30 дней: {top}")
    return f"market refreshed (total {total}, {len(fresh)} new)"


# ---------------------------------------------------------------- 3. RELIABILITY
def health_check():
    """successRate is a Bazaar ranking field, so our own uptime is commercial."""
    results = {}
    for p in ["/health", "/search?q=test", "/join"]:
        try:
            req = urllib.request.Request(SERVICE + p, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=12) as r:
                results[p] = r.status
        except urllib.error.HTTPError as e:
            results[p] = e.code
        except Exception as e:
            results[p] = type(e).__name__
    ok = results.get("/health") == 200 and results.get("/search?q=test") == 402
    con = connect()
    con.execute("INSERT INTO actions(kind,action_class,dry_run,payload,result,created_at) "
                "VALUES (?,?,?,?,?,?)",
                ("health_check", "GREEN", 0, json.dumps(results),
                 "healthy" if ok else "DEGRADED", now()))
    con.commit()
    con.close()
    if ok:
        say("judge", "Проверил свой сервис: 402 отдаётся правильно, страницы живые. "
                     "Это важно — в Bazaar рейтинг зависит от доли успешных ответов.")
    else:
        say("judge", f"⚠ Сервис деградировал: {results}. Пока это не починено, листиться нельзя — "
                     f"плохая надёжность закопает нас в выдаче.")
        note("orchestrator", f"SERVICE DEGRADED: {results}", conf=1.0)
    return f"health {'ok' if ok else 'DEGRADED'} {results}"


# ---------------------------------------------------------------- 4. LEARN
def study_market():
    """Self-directed work when nothing is assigned: mine the crawl for the
    insight the newsletter is supposed to contain."""
    try:
        items = json.loads(IDX.read_text(encoding="utf-8"))
    except Exception:
        return "no index yet"
    if not items:
        return "index empty"
    prices, calls, payers, tags = [], 0, 0, {}
    for i in items:
        a = (i.get("accepts") or [{}])[0]
        amt = a.get("maxAmountRequired") or a.get("amount")
        try:
            prices.append(int(amt) / 1e6)
        except Exception:
            pass
        q = i.get("quality") or {}
        calls += q.get("l30DaysTotalCalls") or 0
        payers += q.get("l30DaysUniquePayers") or 0
        for t in (i.get("tags") or []):
            tags[t] = tags.get(t, 0) + 1
    prices.sort()
    med = prices[len(prices) // 2] if prices else 0
    top = sorted(tags.items(), key=lambda kv: -kv[1])[:5]
    say("verifier", f"Изучил {len(items)} сервисов. Медианная цена ${med:.4f} за вызов. "
                    f"Суммарно {calls:,} вызовов и {payers:,} уникальных плательщиков за 30 дней. "
                    f"Вывод неприятный, но честный: медианный сервис зарабатывает копейки — "
                    f"этого хватает на ДОКАЗАТЕЛЬСТВО, но не на доход.")
    note("verifier", f"Market study of {len(items)} services: median price ${med:.4f}, "
                     f"total 30d calls {calls:,}, total unique payers {payers:,}. "
                     f"Top categories: {', '.join(f'{k}({v})' for k, v in top)}.", conf=0.9)
    return f"studied {len(items)} services (median ${med:.4f})"


# ---------------------------------------------------------------- 5. AUDIT
def audit():
    """Guard the proof's integrity. spend MUST stay empty."""
    con = connect()
    q = lambda s: con.execute(s).fetchone()[0]
    spend = q("SELECT COUNT(*) FROM spend")
    pays = q("SELECT COUNT(*) FROM payments")
    orphan = q("SELECT COUNT(*) FROM evidence e LEFT JOIN sources s ON s.id=e.source_id "
               "WHERE s.id IS NULL")
    con.close()
    if spend:
        say("adversary", f"❌ ДОКАЗАТЕЛЬСТВО СЛОМАНО: в таблице расходов {spend} запись. "
                         f"Заявление «с нуля» больше не действует.")
        note("adversary", f"PROOF INVALIDATED: spend table has {spend} row(s). "
                          f"The $0 claim no longer holds.", conf=1.0)
    if orphan:
        note("adversary", f"DATA INTEGRITY: {orphan} evidence row(s) cite a missing source.", conf=1.0)
    if not spend and not orphan:
        say("adversary", f"Аудит чистый: потрачено 0, платежей {pays}, "
                         f"утверждений без источника нет. Придраться не к чему — пока.")
    return f"audit: spend={spend} payments={pays} orphan_evidence={orphan}"


def mtbx_audit():
    """Полный аудит по спецификации MTBX — 46 механических проверок.

    Запускается самой экосистемой, а не человеком: спецификация требует
    перепроверять всё непрерывно, а не один раз при сдаче. Любая упавшая
    проверка уходит в чат как претензия противника, чтобы механик её увидел.
    """
    r = subprocess.run(["py", "-3.13", "-X", "utf8", "mtbx_audit.py"],
                       cwd=str(ROOT), capture_output=True, text=True, timeout=900)
    out = r.stdout or ""
    fails = [l.strip()[2:].strip() for l in out.splitlines() if l.strip().startswith("- [")]
    m = re.search(r"ИТОГ: (\d+) прошло, (\d+) упало", out)
    ok, bad = (m.group(1), m.group(2)) if m else ("?", "?")
    if fails:
        say("adversary", f"Аудит MTBX: {ok} прошло, {bad} УПАЛО. Первое: {fails[0][:150]}")
        for f in fails[:5]:
            note("adversary", f"MTBX audit failure: {f}", conf=1.0)
    else:
        say("adversary", f"Аудит MTBX пройден целиком: {ok} проверок, ни одной упавшей.")
    return f"mtbx: {ok} ok / {bad} fail"


# Scout тоже работает сам, без кнопки: крутит темы по очереди
RESEARCH_TOPICS = ["llm inference", "web search", "market data price", "twitter social",
                   "onchain wallet balance", "image generation", "news feed", "email verify"]
_topic_i = [0]


def scout_research():
    """Собственная работа Scout в непрерывном цикле — без нажатия кнопки."""
    from agents import scout
    t = RESEARCH_TOPICS[_topic_i[0] % len(RESEARCH_TOPICS)]
    _topic_i[0] += 1
    say("scout", f"Беру следующую тему: «{t}». Смотрю, кто там уже зарабатывает и на чём.")
    r = scout.research_index(t)
    if r.get("error"):
        say("scout", f"По теме «{t}» не смог посмотреть: {r['error']}")
        return f"research failed: {r['error']}"
    top = (r.get("top") or [])[:3]
    if top:
        say("scout", f"«{t}»: {r['matches']} сервисов. Сильнейшие по числу плательщиков — " +
                     ", ".join(f"{h['name'][:32]} ({h['payers30d']})" for h in top))
    return f"research '{t}': {r.get('matches')} matches"


def _growth(fn_name):
    def run():
        from agents import growth
        return dict(growth.CYCLE)[fn_name]()
    return run


def economic_review():
    """Раздел 28: кто не окупается. Раздел 19: гейт дорогих операций."""
    verdicts = economics.survival()
    bad = [v for v in verdicts if not v["verdict"].startswith("оставить")]
    if bad:
        txt = "; ".join(f"{v['agent']} ({v['verdict']})" for v in bad)
        say("optimizer", f"Экономический разбор: не окупаются — {txt}. "
                         f"Предлагаю паузу, но решение за владельцем.")
        note("optimizer", f"AGENT SURVIVAL: underperforming — {txt}", conf=0.9)
        return f"кандидатов на паузу: {len(bad)}"
    return f"все {len(verdicts)} агентов оправдывают работу"


def daily_briefing():
    """Раздел 37: сводка РАЗ В СУТКИ. Раньше выходила каждые 20 шагов и меняющиеся
    числа обходили дедупликацию — получалась имитация новостей."""
    con = connect()
    last = con.execute("SELECT MAX(created_at) FROM messages WHERE topic='chat' "
                       "AND sender='orchestrator' AND body LIKE 'Сводка:%'").fetchone()[0]
    con.close()
    if last:
        from datetime import datetime as _dt
        age = (datetime.now(timezone.utc) - _dt.fromisoformat(last)).total_seconds()
        if age < 20 * 3600:
            return f"сводка была {int(age/3600)} ч назад — рано"
    b = economics.briefing()
    say("orchestrator",
        f"Сводка: выручка ${b['verified_revenue_usd']}, потрачено ${b['spend_usd']}, "
        f"прогонов за сутки {b['runs_24h']} (сбоев {b['failures_24h']}), "
        f"новых находок {b['new_findings_24h']}. {b['stage']} — {b['goal']}")
    if not b["zero_capital_claim_intact"]:
        say("adversary", "ВНИМАНИЕ: появились траты — заявление «с нуля» больше недействительно.")
    return f"{b['stage']}, выручка ${b['verified_revenue_usd']}"


def _craft(fn_name):
    def run():
        from agents import craftsman
        return dict(craftsman.CYCLE)[fn_name]()
    return run


def _bounty(fn_name):
    def run():
        from agents import bounty
        return dict(bounty.CYCLE + bounty.FAST_CYCLE)[fn_name]()
    return run


def _mech(fn_name):
    def run():
        from agents import mechanic
        return dict(mechanic.CYCLE)[fn_name]()
    return run


def _sales(fn_name):
    def run():
        from agents import salesman
        return dict(salesman.CYCLE)[fn_name]()
    return run


def _leads(fn_name):
    def run():
        from agents import leads
        return f"лидов: {len(leads.hot_leads())}"
    return run


def _postman(fn_name):
    def run():
        from agents import postman
        return dict(postman.CYCLE)[fn_name]()
    return run


def _team(fn_name):
    def run():
        from agents import team
        return dict(team.CYCLE)[fn_name]()
    return run


# ЯДРО — то, что реально двигает миссию. Крутится каждый цикл.
# Раздел 2 директивы запрещает держать агентов ради видимости работы,
# раздел 28 требует, чтобы агент окупал прогон. По данным (прогоны/выход):
#   optimizer 52/55 — накрутка: одна и та же фраза с меняющимся счётчиком
#   explorer 151/2, verifier 52/1 — почти ничего, и дублируют разведку
# Они не удалены, а переведены в редкий режим: их польза реальна, но не ежеминутна.
# ЯДРО — раздел 32 директивы «revenue-first prioritization».
# После разворота на услуги приоритет пересобран: сначала то, что ведёт к деньгам,
# потом всё остальное. Разведка клиентов и диагноз — это выручка, они в ядре.
CYCLE = [("watch_payments", watch_payments),        # миссия: первый платёж
         ("watch_prs", _craft("watch_prs")),
         ("fresh_bounties", _bounty("fresh_bounties")),  # скорость = единственное преимущество
         ("hunt_bounties", _bounty("hunt_bounties")),
         ("find_leads", _leads("find_leads")),      # кто может заплатить
         ("diagnose_leads", _sales("diagnose_leads")),  # за что именно заплатит
         ("distributor", _team("distributor")),     # видно ли нас
         ("health_check", health_check),            # аптайм = место в выдаче
         ("refresh_market", refresh_market),        # свежесть данных = свежесть диагнозов
         ("audit", audit),                          # целостность доказательства
         ("watchdog", _team("watchdog"))]           # живость агентов

# РЕДКИЕ — полезны, но не ежеминутно.
SLOW_CYCLE = [("mechanic", _mech("mechanic")),
              ("scout_research", scout_research),
              ("critique", _growth("critique")),
              ("scribe", _team("scribe")),
              ("explorer_replies", _team("explorer_replies")),
              ("mail_sync", _postman("mail_sync")),
              ("mail_advance", _postman("mail_advance")),
              ("explore", _growth("explore")),
              ("explore_alternatives", _growth("explore_alternatives")),
              ("merchant", _team("merchant")),
              ("study_market", study_market),
              ("optimize", _growth("optimize")),
              ("economics", economic_review),
              ("briefing", daily_briefing),
              ("mtbx_audit", mtbx_audit)]
SLOW_EVERY = 20   # один редкий шаг на каждые 20 быстрых


def run_forever(interval=90):
    print(f"[worker] starting; cycle every {interval}s. KILL_SWITCH halts it.", flush=True)
    i = 0
    while True:
        try:
            guard.check_alive()
        except Exception as e:
            print(f"[worker] HALTED: {e}", flush=True)
            return
        # каждые SLOW_EVERY шагов — один редкий вместо быстрого
        if i and i % SLOW_EVERY == 0:
            name, fn = SLOW_CYCLE[(i // SLOW_EVERY - 1) % len(SLOW_CYCLE)]
        else:
            name, fn = CYCLE[i % len(CYCLE)]
        agent = AGENT_OF.get(name, "orchestrator")
        started = now()
        try:
            out = fn()
            record_run(name, agent, True, str(out), started, now())
            print(f"[{datetime.now().strftime('%H:%M:%S')}] {name}: {out}", flush=True)
        except Exception as e:
            detail = f"{type(e).__name__}: {e}"
            record_run(name, agent, False, detail, started, now())
            say(agent, f"⚠ Шаг «{name}» упал: {detail[:130]}. Записал в журнал, "
                       f"чтобы это не потерялось и попало в мою репутацию.")
            print(f"[{datetime.now().strftime('%H:%M:%S')}] {name} FAILED: {detail}", flush=True)
        i += 1
        time.sleep(interval / len(CYCLE))


if __name__ == "__main__":
    run_forever(int(sys.argv[1]) if len(sys.argv) > 1 else 90)
