"""CONTINUOUS WORKER — agents never idle.

Cycle order is deliberate: the mission first (watch for payment), then keep the
product's data fresh, then guard our own reliability, then learn. When there is
no assigned work, agents do self-directed work rather than sit still.

All work here is GREEN (reversible, private, free). Judgment still escalates -
this loop never decides anything, it gathers and measures.
"""
import sys, re, json, time, sqlite3, subprocess, urllib.request, urllib.error
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.db import connect
from core import guard, memory, economics, telemetry

# НИ ОДИН ДОЧЕРНИЙ ПРОЦЕСС НЕ ОТКРЫВАЕТ ОКНО.
# Окна выскакивали не из запуска воркера, а из КАЖДОГО вызова gh, git, node и
# powershell: процесс без собственной консоли заводит новое окно на каждый
# такой вызов. Их двадцать, и правка по местам гарантировала бы двадцать
# первый. Флаг ставится один раз на весь процесс.
try:
    from core.launch import silence as _silence
    _silence()
except Exception:
    pass


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
        # БЕЗ ОКНА ПО ВРЕМЕНИ. Раньше дубль пропускался только в пределах шести
        # часов, и та же реплика возвращалась на седьмом — а уборка повторов их
        # тут же удаляла. Две части системы работали друг против друга: одна
        # разрешала повтор, другая его стирала, и в чате всё равно копились
        # дубли (тридцать из четырёхсот шестидесяти шести на замере).
        # Подтверждение жизни агентов берётся из журнала прогонов, а не из
        # повторённой реплики, поэтому окно не нужно вовсе.
        dup = con.execute("SELECT 1 FROM messages WHERE topic='chat' AND body=? LIMIT 1",
                          (text,)).fetchone()
        con.close()
        if dup:
            return
    con = connect()
    con.execute("INSERT INTO messages(sender,recipient,topic,body,created_at) VALUES (?,?,?,?,?)",
                (agent, None, topic, text, now()))
    con.commit()
    con.close()


def note(agent, claim, source_id=None, conf=None):
    """След оставляется только для НОВОГО вывода. Дубли не пишутся.

    Проверка идёт в два слоя, и второй важнее. Точное совпадение ловит
    дословный повтор, а СМЫСЛОВОЕ — тот же вывод, сформулированный иначе.
    Без второго слоя система заново приходит к собственным заключениям
    другими словами и засчитывает это себе как новую находку.
    """
    if memory.seen_claim(claim):
        return
    try:
        from core import recall as _rc
        known = _rc.already_known(claim)
        if known:
            return          # к этому выводу уже приходили, пусть и иначе
    except Exception:
        pass
    con = connect()
    if source_id is None:
        source_id = con.execute(
            "INSERT INTO sources(url,title,fetched_at,raw_excerpt) VALUES (?,?,?,?)",
            (f"worker://{agent}", f"worker cycle {agent}", now(), claim[:400])).lastrowid
    con.execute("INSERT INTO evidence(claim,source_id,agent,created_at,confidence) VALUES (?,?,?,?,?)",
                (claim, source_id, agent, now(), conf))
    con.commit()
    con.close()


AGENT_OF = {"reason_and_act":"orchestrator","expand":"prospector","fulfil":"craftsman","deep_check":"prospector","escalation_watch":"orchestrator","housekeeping":"orchestrator","pursue":"craftsman","mtbx_audit":"adversary","prospect":"prospector","probe_paths":"prospector","path_report":"prospector","find_channel":"leads","verify_service":"leads","collect_payouts":"craftsman","fresh_bounties":"bounty","watch_prs":"craftsman","find_doc_work":"craftsman","hunt_bounties":"bounty","mechanic":"mechanic","find_leads":"leads","diagnose_leads":"salesman","mail_sync":"postman","mail_advance":"postman","economics":"optimizer","briefing":"orchestrator","merchant":"merchant","distributor":"distributor","scribe":"scribe",
            "watchdog":"watchdog","explorer_replies":"explorer",
            "watch_payments":"orchestrator","refresh_market":"scout","scout_research":"scout",
            "health_check":"judge","explore":"explorer","study_market":"verifier",
            "critique":"critic","audit":"adversary","optimize":"optimizer",
            "explore_alternatives":"explorer"}


def _iso(t):
    """Приводит метку времени к UTC с поясом.

    Разные вызывающие писали в runs то наивное время, то с поясом, и сравнение
    падало с TypeError прямо внутри проверки живости.

    Наивная метка считается МЕСТНЫМ временем и переводится в UTC. Прежняя
    версия просто дописывала «+00:00», то есть объявляла местное время
    всемирным — и записи уезжали в будущее на разницу поясов. Ошибка тем
    хуже, что данные выглядят исправными.
    """
    from datetime import datetime as _dt
    t = str(t)
    if "+" in t[10:] or t.endswith("Z"):
        return t
    try:
        return _dt.fromisoformat(t).astimezone(timezone.utc).isoformat()
    except ValueError:
        # Метка не разбирается — значит мы не знаем, когда это было. Дописать
        # к ней пояс значит выдумать ответ и получить данные, которые выглядят
        # исправными. Честнее записать момент, когда мы это увидели.
        return now()


def record_run(step, agent, ok, detail, started, ended, _retry=3):
    """Каждый шаг цикла попадает в журнал. Без этого 'агенты работают' - слова.

    ЗАПИСЬ В ЖУРНАЛ НЕ ИМЕЕТ ПРАВА УРОНИТЬ ЦИКЛ. Именно это и произошло:
    воркер поймал ошибку шага, пошёл записать её в журнал, получил
    «database is locked» — и упал целиком, потому что падение случилось
    внутри обработчика ошибок. Экосистема простояла мёртвой, а снаружи
    это выглядело просто как отсутствие свежих записей.
    """
    try:
        return _record_run(step, agent, ok, detail, started, ended)
    except sqlite3.OperationalError as e:
        if _retry > 0 and "locked" in str(e).lower():
            time.sleep(1.5)
            return record_run(step, agent, ok, detail, started, ended, _retry - 1)
        # журнал потерян, но цикл продолжается: работа важнее записи о работе
        print(f"[worker] запись в журнал не удалась ({e}); шаг «{step}» продолжается",
              flush=True)
        return None


def _record_run(step, agent, ok, detail, started, ended):
    con = connect()
    con.execute("INSERT INTO runs(agent,started_at,ended_at,status,notes) VALUES (?,?,?,?,?)",
                (agent, _iso(started), _iso(ended), "ok" if ok else "error",
                 f"{step}: {detail}"[:400]))
    # репутация считается ТОЛЬКО по исходам, а не по красноречию
    con.execute("""INSERT INTO agent_reputation(agent,role,calls,correct,updated_at)
                   VALUES (?,?,1,?,?)
                   ON CONFLICT(agent,role) DO UPDATE SET
                     calls=calls+1, correct=correct+?, updated_at=?""",
                (agent, step, 1 if ok else 0, now(), 1 if ok else 0, now()))
    con.commit()
    con.close()
    # Стоимость шага в учёт. Без неё survival() и agent_roi() считали отдачу
    # по пустым затратам, то есть по определению не могли никого забраковать.
    try:
        economics.record_cost(agent, "cycle_step", 1, 0.0, step)
    except (ValueError, KeyError):
        pass


def wallet():
    """Адрес, на который ждём платёж. Нет настройки — нет адреса, а не крушение.

    Файл настроек не хранится в репозитории (в нём ключи), поэтому в облаке его
    нет по замыслу. Раньше чтение шло без обработки, и в облаке падал тест —
    красный прогон на ограничении среды, а не на дефекте. Такой отчёт учит не
    доверять отчёту целиком.

    Отсутствие адреса возвращается как отсутствие: тот, кто спрашивает, обязан
    отличить «кошелька нет» от «кошелёк пуст», и здесь это различие сохранено.
    """
    env = ROOT / ".env"
    if not env.exists():
        return None
    for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
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
        from core import events
        events.publish("payment_received", {"count": new, "address": addr},
                       source="worker")
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


_REASON_I = [0]


# Какое событие чьим шагом отрабатывается. Событие без обработчика будет
# честно висеть в очереди, а не считаться разобранным.
EVENT_HANDLER = {
    "pr_review_arrived": "watch_prs",
    "payout_announced": "collect_payouts",
    "payment_received": "watch_payments",
    "fresh_bounty": "pursue",
    "invariant_broken": "mechanic",
    "worker_down": "watchdog",
    "new_open_path": "probe_paths",
    "lead_defect_found": "verify_service",
    "escalation_answered": "fulfil",
    "market_changed": "refresh_market",
}


def _take_event():
    """Берёт самое срочное ждущее событие и возвращает шаг, который его закроет.

    Возвращает (имя_шага, функция, агент) или None. Событие помечается
    выполненным ТОЛЬКО после отработки шага — брошенное посреди работы
    вернётся в очередь следующему, а не пропадёт молча.
    """
    try:
        from core import events
        todo = events.pending(limit=1)
    except Exception:
        return None
    if not todo:
        return None
    e = todo[0]
    step = EVENT_HANDLER.get(e["kind"])
    steps = dict(CYCLE + SLOW_CYCLE)
    if not step or step not in steps:
        return None
    agent = AGENT_OF.get(step, "orchestrator")
    if not events.claim(e["id"], agent):
        return None
    _PENDING_EVENT[0] = e["id"]
    print(f"[worker] СОБЫТИЕ {e['kind']} (срочность {e['priority']}) "
          f"-> вне очереди «{step}»", flush=True)
    return step, steps[step], agent


_PENDING_EVENT = [None]


def reason_and_act():
    """Оборот РАССУЖДАЮЩЕГО агента: он сам выбирает, что делать дальше.

    Чем это отличается от остальных шагов цикла. Все прочие шаги — жёсткая
    последовательность: седьмой идёт после шестого, всегда. Здесь агент
    смотрит на своё состояние — что он уже пробовал, что не сработало, что
    висит у него в очереди — и выбирает следующее действие сам, объясняя
    выбор своими словами.

    Граница честная: выбирает он ТОЛЬКО из своего белого списка обратимых
    действий класса GREEN. Публикация, отправка наружу, трата — по-прежнему
    через эскалацию. Это выбор внутри одобренного меню, а не свобода.

    Агенты берутся по очереди, чтобы каждый получал слово.
    """
    from core import roster, agent      # noqa: F401  (импорт регистрирует состав)
    names = sorted(agent.REGISTRY)
    if not names:
        return "рассуждающих агентов нет"
    name = names[_REASON_I[0] % len(names)]
    _REASON_I[0] += 1
    a = agent.get(name)
    r = a.act()
    if r.get("ok"):
        say(name, f"Решил сам: беру «{r['chose']}». Почему: {r.get('why','')[:160]}")
    return f"{name} -> {r.get('chose')}: {str(r.get('detail'))[:80]}"


def housekeeping():
    """Включает механизмы, которые существовали и никогда не вызывались.

    Детектор мёртвого кода нашёл пятнадцать таких функций. Часть из них —
    не украшения, а несущие узлы, которые просто никто не дёргал:

      execution.stalled()         кто завис в работе и не двигается
      execution.unblock()         снятие блокера, когда причина исчезла
      memory.cleanup_duplicates() уборка повторов, накопившихся до дедупликации
      bus.my_work() / answers_for() входящие агентов — их никто не читал,
                                  то есть вопросы задавались в пустоту

    Функция, которую не вызывают, работой не является — ровно как заранее
    записанный текст. Разница только в том, что первую хотя бы написали
    добросовестно.
    """
    from core import execution, memory as mem, bus, router
    lines = []

    # БРОШЕННЫЕ СОБЫТИЯ. Событие, взятое в работу и не подтверждённое, — это
    # тишина, неотличимая от «всё сделано». Возвращаем их в очередь, иначе
    # срочное пропадает молча, а именно ради срочного шина и заводилась.
    try:
        from core import events as _ev
        lost = _ev.stale(minutes=30)
        for e in lost:
            _ev.release(e["id"])
        if lost:
            lines.append(f"возвращено брошенных событий: {len(lost)}")
    except Exception:
        pass

    # ЛОКАЛЬНАЯ МОДЕЛЬ. Она лежала, и этого не заметил ни один аудит: шаги,
    # ходящие через неё, просто падали по одному, а картина в целом выглядела
    # рабочей. Отсутствие модели — это не мелочь: через неё идут все
    # механические задачи, а суждения по протоколу обязаны идти мимо неё.
    ok, detail = router.health()
    if not ok:
        lines.append("локальная модель НЕ ОТВЕЧАЕТ")
        say("orchestrator", f"Локальная модель не отвечает ({detail[:70]}). Механические "
                            f"задачи через неё сейчас падают. Это чинится запуском ollama, "
                            f"и до починки я не выдаю их результаты за полученные.")
        note("orchestrator", f"LOCAL MODEL DOWN: {detail[:200]}", conf=1.0)

    stuck = execution.stalled(hours=6)
    if stuck:
        lines.append(f"зависших задач {len(stuck)}")
        say("orchestrator", f"Висят без движения дольше 6 часов: {len(stuck)} задач. "
                            f"Верхняя — «{(stuck[0].get('objective') or '')[:60]}». "
                            f"Зависшая задача это не работа в процессе, это остановка.")
        note("orchestrator", f"STALLED TASKS: {len(stuck)} in progress with no movement "
                             f"for over 6 hours", conf=1.0)

    cleaned = mem.cleanup_duplicates()
    if cleaned.get("removed"):
        lines.append(f"убрано повторов: {cleaned['removed']}")

    # ВХОДЯЩИЕ. Вопросы задавались, ответы приходили, читать их было некому.
    unread = 0
    for agent in ("orchestrator", "craftsman", "bounty", "prospector", "leads", "salesman"):
        unread += len(bus.my_work(agent)) + len(bus.answers_for(agent, limit=20))
    if unread:
        lines.append(f"непрочитанных входящих {unread}")

    return "; ".join(lines) if lines else "порядок: зависших нет, повторов нет"


def escalation_watch():
    """Сколько суждений ждёт ответа. Механизм был, потребителя не было.

    escalate() создавал запись, pending_escalations() её читал — и не вызывался
    ниоткуда. То есть вопросы, которые локальной модели решать запрещено,
    уходили в таблицу и лежали там молча. Молчащая очередь суждений опаснее
    пустой: со стороны она выглядит как «решать нечего».
    """
    from agents import council
    pend = council.pending_escalations()
    if not pend:
        return "суждений на эскалации нет"
    oldest = pend[0].get("created_at", "")
    say("orchestrator", f"На эскалации ждут ответа {len(pend)} суждений, старейшее от "
                        f"{oldest[:16]}. Это вопросы, которые локальной модели решать "
                        f"запрещено. Они видны в очереди на дашборде.")
    note("orchestrator", f"PENDING JUDGMENT: {len(pend)} escalation(s) awaiting a "
                         f"frontier-model answer; oldest {oldest}", conf=1.0)
    return f"на эскалации {len(pend)} суждений"


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
    # Раз в четыре темы — ПОЛНЫЙ разбор с чтением страниц, а не только индекс.
    # run_job() умел это с самого начала и не вызывался ниоткуда.
    if _topic_i[0] % 4 == 0:
        ids = scout.run_job(f"кто зарабатывает на теме «{t}» и на чём именно",
                            [f"{t} api pricing", f"{t} paid service"], max_pages=2)
        say("scout", f"Полный разбор темы «{t}»: собрано утверждений {len(ids or [])}.")
        return f"глубокий разбор «{t}»: утверждений {len(ids or [])}"
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
    roi = economics.agent_roi()          # отдача по каждому: считалась, но не смотрелась
    bad = [v for v in verdicts if not v["verdict"].startswith("оставить")]
    if bad:
        txt = "; ".join(f"{v['agent']} ({v['verdict']})" for v in bad)
        say("optimizer", f"Экономический разбор: не окупаются — {txt}. "
                         f"Предлагаю паузу, но решение за владельцем.")
        note("optimizer", f"AGENT SURVIVAL: underperforming — {txt}", conf=0.9)
        return f"кандидатов на паузу: {len(bad)}"
    return f"все {len(verdicts)} агентов оправдывают работу; отдача посчитана по {len(roi)}"


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


def _prospect(fn_name):
    def run():
        from agents import prospector
        return dict(prospector.CYCLE)[fn_name]()
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
    """Диспетчер шагов разведки клиентов.

    Здесь была тихая поломка: он игнорировал имя шага и всегда звал hot_leads().
    Поэтому find_channel и verify_service, вписанные в цикл, не запустились бы
    НИКОГДА, а журнал показывал бы «лидов: 25» и выглядел работающим. Именно
    это и давало 100% одинаковых прогонов у агента leads.
    """
    def run():
        from agents import leads
        return dict(leads.CYCLE)[fn_name]()
    return run


def _postman(fn_name):
    def run():
        from agents import postman
        return dict(postman.CYCLE)[fn_name]()
    return run


def advance_tasks():
    """Двигает очередь задач вперёд. До этого её не двигал НИКТО.

    Что было. Очередь заполнялась — девять задач, у шести «близость к деньгам»
    первая и вторая, — и стояла нетронутой двадцать шесть часов. Причина не в
    запретах и не в нехватке работы: у execution.start() и execution.next_task()
    не было НИ ОДНОГО вызова в рабочем коде. Их звал только самопроверочный
    аудит на своей же тестовой строке. Конвейер был построен, отлажен и не
    подключён к мотору.

    Признак, по которому это можно было заметить раньше, лежал на виду: у всех
    шести задач attempts равнялось нулю. Счётчик попыток растёт при входе в
    «в работе», и ноль означает буквально «никто ни разу не пробовал».

    Что делает шаг. Берёт задачу, которая ближе всего к деньгам, переводит её
    в работу и передаёт следующее действие на эскалацию — потому что почти
    всё, что там лежит, требует суждения или обращения наружу, а это не
    уровень агента. Зато задача перестаёт быть невидимой: она числится
    начатой, у неё растут попытки, и её видно в очереди на дашборде.

    Чего шаг НЕ делает: не выполняет само действие и не притворяется, что
    выполнил. Сдвинуть задачу и объявить её сделанной — это тот же обман
    продуктивности, только на уровне конвейера.
    """
    from core import execution
    from agents import council

    t = execution.next_task()
    if not t:
        return "очередь пуста — двигать нечего"

    # Уже начатую не трогаем: попытки должны считать настоящие заходы.
    if t.get("attempts", 0) and t.get("state") == "running":
        return f"#{t['id']} уже в работе, попыток {t['attempts']}"

    # УПАВШУЮ ЗАДАЧУ НЕЛЬЗЯ НАЧАТЬ НАПРЯМУЮ. next_task() отдаёт и ждущие, и
    # упавшие, а машина состояний разрешает из failed только возврат в очередь:
    # провал обязан породить следующую ПОПЫТКУ, а не продолжение прежней. Без
    # этого шага шаг падал с InvalidTransition тридцать один раз за восемь
    # минут — я сам это и внёс, добавляя потребителя очереди.
    # СОСТОЯНИЕ БЕРЁТСЯ ИЗ БАЗЫ, А НЕ ИЗ ВЫДАЧИ ОЧЕРЕДИ: next_task() возвращает
    # id, цель, следующее действие, владельца, близость к деньгам и попытки —
    # состояния среди них нет. Проверка t.get("state") была всегда ложной, и
    # ветка возврата в очередь не срабатывала ни разу.
    con = connect()
    row = con.execute("SELECT state FROM tasks WHERE id=?", (t["id"],)).fetchone()
    con.close()
    if row and row[0] == "failed":
        execution.unblock(t["id"], "новая попытка после провала")
    execution.start(t["id"], "взята в работу очередью")
    qid = council.escalate(
        "orchestrator",
        f"Задача #{t['id']}: {t['objective']}. Следующее действие: "
        f"{t.get('next_action') or 'не указано'}.",
        context=json.dumps({"task_id": t["id"], "money_proximity": t.get("money_proximity")},
                           ensure_ascii=False))
    say("orchestrator", f"Взял в работу задачу #{t['id']}: {str(t['objective'])[:70]}. "
                        f"Следующее действие требует вашего решения — отправил в очередь "
                        f"суждений (№{qid}).")
    return f"#{t['id']} взята в работу, следующее действие на эскалации №{qid}"


def _src(tool_name):
    """Шаг, исполняющий подключение к бесплатному источнику.

    Ходит через реестр инструментов, а не напрямую: так шаг цикла и инструмент
    агента — это ОДИН И ТОТ ЖЕ код. Иначе они расходятся, и агент рассуждает о
    возможности, которая в цикле давно сломана.
    """
    def run():
        from core import roster  # noqa: F401  — регистрация инструментов
        from core.agent import TOOLS
        return TOOLS[tool_name].fn()
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
         ("advance_tasks", advance_tasks),           # двигает очередь — её не двигал никто
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
              ("prospect", _prospect("prospect")),          # ищет ВСЕ пути к деньгам
              ("expand", _prospect("expand")),
              ("deep_check", _prospect("deep_check")),
              ("probe_paths", _prospect("probe_paths")),    # щупает их о наши стены
              ("path_report", _prospect("path_report")),
              ("find_channel", _leads("find_channel")),
              ("verify_service", _leads("verify_service")),
              ("collect_payouts", _craft("collect_payouts")),
              ("pursue", _craft("pursue")),
              ("fulfil", _craft("fulfil")),
              ("reason_and_act", reason_and_act),
              ("housekeeping", housekeeping),
              ("escalation_watch", escalation_watch),
              ("mtbx_audit", mtbx_audit),
              # Бесплатные подключения. Каждое проверено живым вызовом, у
              # каждого есть агент-владелец; здесь они получают ход в цикле,
              # иначе остались бы возможностью, которой никто не пользуется.
              # Конкурсы переехали сюда из ядра: ядро обязано оставаться узким, а
              # конкурс платит ОДНОМУ победителю — это слабее прямой очереди задач.
              ("hunt_contests", _src("hunt_contests")),    # конкурсы с призовым фондом
              ("hunt_hn_jobs", _src("hunt_hn_jobs")),      # вакансии без ключа
              ("market_demand", _src("market_demand")),    # спрос, измеренный чужими руками
              ("chain_economics", _src("chain_economics")),  # выручка в долларах, не в токенах
              ("rich_targets", _src("rich_targets")),      # у кого есть деньги
              ("package_docs", _src("package_docs")),      # сырьё для работы «документация»
              ("supply_check", _src("supply_check")),     # перекличка снабжения
              ("where_time_goes", _src("where_time_goes")),  # куда уходит время
              # Поиск работы класса «документация» был объявлен у мастерового и
              # НЕ ВПИСАН сюда: за сутки ноль запусков при сорока шести живых
              # шагах. Способность, которую никто не вызывает, работой не является.
              ("find_doc_work", _craft("find_doc_work")),
              # ИСПОЛНЕНИЕ. Система впервые умеет не только находить работу, но
              # и делать её: извлекать интерфейс проекта разбором кода и
              # сверять каждое утверждение обратно с исходником.
              ("produce_work", _src("produce_work")),
              ("check_work", _src("check_work"))]
SLOW_EVERY = 20   # один редкий шаг на каждые 20 быстрых

# ═══════════════════════════════════════ ЧТО МОЖЕТ РАБОТАТЬ В ОБЛАКЕ
#
# Владелец спросил, почему всё крутится локально. Честный ответ на момент
# вопроса: в облаке работали ТРИ шага из тридцати семи — проверка кошелька,
# обновление рынка и аудит целостности. Остальное требовало либо локального
# сервиса, либо локальной модели, либо просто не было туда вписано. Называть
# это «круглосуточной работой в облаке» было сильным преувеличением.
#
# Здесь честная граница. В облаке работает всё, чему нужны только сеть, база
# и gh (он есть на раннерах GitHub). НЕ работает то, что упирается в машину
# владельца, и это названо поимённо, а не умолчано.
CLOUD_STEPS = [
    "watch_payments",      # миссия: не пришёл ли платёж
    "advance_tasks",       # очередь задач должна двигаться и в облаке
    "refresh_market",      # свежесть рыночных данных
    "audit",               # целостность доказательства
    "fresh_bounties",      # перехват свежих премий — здесь решает скорость
    "hunt_bounties",       # полный обход рынка задач
    "watch_prs",           # состояние отправленной работы
    "collect_payouts",     # объявлена ли нам выплата
    "pursue",              # ведение задачи до заявки
    "fulfil",              # отправка готовой работы
    "find_leads",          # кто может заплатить
    "diagnose_leads",      # за что именно
    "find_channel",        # чем до них дотянуться законно
    "verify_service",      # есть ли повод для обращения
    "prospect",            # разведка классов заработка
    "probe_paths",         # проверка площадок о наши стены
    "path_report",         # картина путей целиком
    "expand",              # расширение пространства поиска рынком
    "distributor",         # видит ли нас рынок
    "housekeeping",        # зависшие задачи, уборка, входящие
    "escalation_watch",    # суждения, ждущие ответа
    # Ниже — то, что я забыл разобрать при первом переносе. Тринадцать шагов
    # не числились ни в облачных, ни в домашних: они просто выпали из учёта,
    # и «в облаке работает половина» было заниженной оценкой по небрежности,
    # а не по существу. Всем им нужны только база и сеть.
    "watchdog",            # кто из агентов молчит
    "critique",            # утверждения без источника, брошенные предложения
    "scribe",              # летопись изменений
    "explore",             # ниши, где спрос выше конкуренции
    "explore_alternatives",# антитуннельный обзор по измеренной картине
    "merchant",            # ценовые полосы рынка
    "study_market",        # медиана, распределение спроса
    "optimize",            # где система тратит силы впустую
    "economics",           # кто окупается
    "briefing",            # суточная сводка
    "explorer_replies",    # ответы на входящие
    # Бесплатные подключения: всем нужна только сеть, значит облако их тянет.
    "hunt_contests",       # конкурсы с призовым фондом
    "hunt_hn_jobs",        # вакансии и заказы без ключа
    "market_demand",       # где люди дописывают недостающее руками
    "chain_economics",     # состояние сети оплаты и курсы
    "rich_targets",        # организации с деньгами и продуктом
    "package_docs",        # свежесть пакетов и ссылки на репозитории
    "supply_check",        # жива ли вообще наша бесплатная снасть
    "where_time_goes",     # замеры: что дорого, что падает, что тормозит
    "find_doc_work",       # работа класса «документация»
    "produce_work",        # СДЕЛАТЬ работу, а не только найти
    "check_work",          # сверить сделанное с исходниками
]

# Чего в облаке нет и почему — без умолчаний:
CLOUD_CANNOT = {
    "mail_sync": "нужен ключ EmailOctopus, в облачные секреты не передан",
    "mail_advance": "тот же ключ",
    "reason_and_act": "рассуждение агентов идёт через локальную модель",
    "health_check": "проверяет локальный сервис на 127.0.0.1",
    "scout_research": "идёт через локальную языковую модель",
    "deep_check": "тоже через локальную модель",
    "mechanic": "правит код и публикует — из облака это менять репозиторий на ходу",
    "mtbx_audit": "часть проверок обращается к локальному сервису",
}


# Сколько раз подряд шаг может выдать ОДИН И ТОТ ЖЕ результат, прежде чем
# признать, что он ничего нового не приносит.
SAME_LIMIT = 3
# Потолок паузы — сутки, и считается она в МИНУТАХ, а не в оборотах цикла.
# Оборот — счётчик внутри одного запуска; привязывать к нему то, что обязано
# пережить перезапуск, бессмысленно по смыслу, и именно на этом защита от
# повторов не срабатывала ни разу.
BACKOFF_MAX_MIN = 24 * 60

# НО НЕ ДЛЯ ВСЕХ. Общий потолок в сутки едва не обошёлся дорого: защита от
# повторов усыпила watch_payments на 24 часа — то есть проверку того самого
# события, ради которого всё построено. Повторяющийся ответ «платежей нет»
# честен и скучен, но цена пропуска здесь несимметрична: мы ищем ПЕРВЫЙ
# платёж, и узнать о нём через сутки — почти то же, что не узнать.
#
# Отсюда правило: длина паузы определяется не тем, как часто шаг повторяется,
# а тем, ЧЕМ ГРОЗИТ ПРОПУСК. Где цена пропуска высока, скука терпится.
PAUSE_CAP_MIN = {
    "watch_payments":  15,   # приход денег — событие, ради которого всё это
    "fresh_bounties":  20,   # премия живёт часы; опоздание = ноль
    "watch_prs":       60,   # конфликт в нашей работе ждать не должен
    "hunt_bounties":   60,   # полный обход рынка тяжелее, час терпит
    "supply_check":   180,   # снабжение меняется медленно
    # Эти двое ищут, КТО заплатит и ЗА ЧТО. Без своего потолка они падали на
    # общий суточный и засыпали на день — при том, что стоят в ядре цикла
    # именно как путь к выручке. Повтор ответа у них скучен, цена пропуска
    # высока: это та же несимметричность, что у проверки платежа.
    "advance_tasks":   30,   # очередь не должна застывать надолго
    "find_leads":      45,
    "diagnose_leads":  45,
}
# На сколько оборотов он после этого уходит на паузу. Растёт с каждым повтором,
# но не бесконечно: раз в сутки проверить состояние обязан любой шаг.
BACKOFF_MAX = 60
# Паузы повторяющихся шагов ЖИВУТ В БАЗЕ (core.memory), а не здесь. Словарь в
# процессе умирал при каждом перезапуске сторожем, и защита от повторов
# формально работала, фактически не срабатывая ни разу.


def should_run(name):
    """Пропускать ли шаг, который перестал приносить новое.

    Зачем. Детектор подделки нашёл агентов, выдающих один и тот же результат
    прогон за прогоном: «1 PR открыты, изменений нет», «лидов: 25». Это не
    ложь — это правда, повторяемая каждую минуту. Но в журнале она выглядит
    как непрерывная работа, а по существу её там нет.

    ПОЧЕМУ СЧЁТ ХРАНИТСЯ В БАЗЕ, А НЕ В ПАМЯТИ. Первая версия держала счётчик
    повторов в словаре процесса, и он обнулялся при каждом перезапуске. Сторож
    жизни перезапускает воркер, воркер начинал считать заново — и шаг успевал
    повториться по три раза между перезапусками, набирая по журналу семь
    подряд. Защита формально работала и фактически не срабатывала. Это тот же
    класс ошибки, что и всё остальное сегодня: состояние, не пережившее
    перезапуск. Механизм для этого уже был построен — cycle_memory хранит
    результат шага между запусками, им и пользуемся.
    """
    agent = AGENT_OF.get(name, "orchestrator")
    until = memory.paused_until(agent, name)
    return until is None


def note_result(name, out):
    """Учитывает, принёс ли шаг новое, и назначает паузу если нет.

    Счёт повторов ведёт memory.changed(): он лежит в базе и переживает
    перезапуск процесса.
    """
    agent = AGENT_OF.get(name, "orchestrator")
    fresh, seen = memory.changed(agent, name, str(out))
    if seen <= 1:
        memory.resume(agent, name)          # принёс новое — пауза снимается
        return None
    if seen >= SAME_LIMIT:
        # Пауза растёт с числом повторов, но не бесконечно: раз в сутки
        # состояние обязан проверить любой шаг, даже самый скучный.
        cap = PAUSE_CAP_MIN.get(name, BACKOFF_MAX_MIN)
        minutes = min(cap, 5 * 2 ** (seen - SAME_LIMIT))
        memory.pause(agent, name, minutes)
        return minutes
    return None


LOCK = ROOT / "data" / "worker.pid"


def _claim_slot():
    """Единственность воркера обеспечивает ОН САМ, а не тот, кто его запускает.

    Сторож проверял число процессов снаружи и проигрывал гонке: он запускается
    по расписанию и вручную одновременно, а новый воркер стартует не мгновенно,
    поэтому проверка успевала увидеть ноль там, где процесс уже поднимался. За
    час так накопилось восемь воркеров, писавших в одну базу.

    Замок с номером процесса снимает гонку целиком: сколько бы раз воркер ни
    запустили, второй экземпляр увидит живого первого и молча уйдёт.
    """
    import os
    if LOCK.exists():
        try:
            pid = int(LOCK.read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            pid = None
        if pid and pid != os.getpid():
            alive = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"(Get-Process -Id {pid} -ErrorAction SilentlyContinue | "
                 f"Measure-Object).Count"],
                capture_output=True, text=True, timeout=60)
            if (alive.stdout or "0").strip() not in ("", "0"):
                print(f"[worker] уже работает процесс {pid} — второй экземпляр не нужен",
                      flush=True)
                return False
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    LOCK.write_text(str(os.getpid()), encoding="utf-8")
    return True


def _slow_cursor(value=None):
    """Где мы в очереди редких шагов. Хранится в базе, а не в процессе.

    ПОЧЕМУ ЭТО ВАЖНО. Редких шагов тридцать пять, и один из них берётся раз в
    двадцать быстрых оборотов: полный круг занимает около двенадцати часов.
    Счётчик оборотов был обычной переменной и обнулялся при каждом запуске —
    значит после любого перезапуска очередь начиналась С НАЧАЛА.

    Последствие измеримо: первые два редких шага отработали по сорок с лишним
    раз за сутки, а последние семь — от нуля до семнадцати. Не потому, что они
    менее полезны, а потому, что до них просто не доходила очередь. Шаг,
    стоящий в конце списка, при частых перезапусках не выполняется никогда —
    и выглядит при этом исправно объявленным.

    Это ровно та же болезнь, что была у пауз: состояние, не пережившее
    перезапуск, создаёт видимость работы механизма, которого нет.
    """
    con = connect()
    con.execute("CREATE TABLE IF NOT EXISTS cursors ("
                "name TEXT PRIMARY KEY, value INTEGER NOT NULL, at TEXT)")
    if value is None:
        row = con.execute("SELECT value FROM cursors WHERE name='slow'").fetchone()
        con.close()
        return row[0] if row else 0
    con.execute("INSERT INTO cursors(name,value,at) VALUES ('slow',?,?) "
                "ON CONFLICT(name) DO UPDATE SET value=excluded.value, at=excluded.at",
                (int(value), now()))
    con.commit(); con.close()
    return value


def run_forever(interval=90):
    if not _claim_slot():
        return
    print(f"[worker] starting; cycle every {interval}s. KILL_SWITCH halts it.", flush=True)
    # Отметка старта. Без неё счёт повторов тянется через перезапуски и наказывает
    # за поведение, которое уже исправлено, — то есть превращается в цифру,
    # которую хочется подкрутить вместо того, чтобы чинить систему.
    record_run("worker_start", "orchestrator", True,
               f"цикл {interval}с, шагов {len(CYCLE)}+{len(SLOW_CYCLE)}", now(), now())
    i = 0
    # Очередь редких шагов продолжается с того места, где её прервали.
    slow_at = _slow_cursor()
    while True:
        try:
            guard.check_alive()
        except Exception as e:
            print(f"[worker] HALTED: {e}", flush=True)
            return
        # каждые SLOW_EVERY шагов — один редкий вместо быстрого
        if i and i % SLOW_EVERY == 0:
            name, fn = SLOW_CYCLE[slow_at % len(SLOW_CYCLE)]
            slow_at = _slow_cursor(slow_at + 1)
        else:
            name, fn = CYCLE[i % len(CYCLE)]
        # СРОЧНОЕ ИДЁТ ВНЕ ОЧЕРЕДИ. До этого цикл крутил тридцать девять шагов
        # по кругу, и появившаяся работа — пришло ревью, найдена свежая премия,
        # нарушен инвариант — ждала своего оборота. Агент, узнающий о срочном
        # через сорок минут, не реагирует, а отчитывается задним числом.
        urgent = _take_event()
        if urgent:
            name, fn, agent = urgent
        agent = AGENT_OF.get(name, "orchestrator") if not urgent else agent
        ran = False
        try:
            ran = _turn(name, fn, agent, i)
        except Exception as e:
            # ПОСЛЕДНИЙ РУБЕЖ. Что бы ни случилось на обороте — цикл живёт.
            # Ровно здесь экосистема и умирала: шаг упал, воркер пошёл записать
            # это в журнал, получил «database is locked» прямо в обработчике
            # ошибок и завершился целиком. Снаружи это выглядело как тишина.
            # Агент, которого убивает одна ошибка, — не автономный агент.
            print(f"[worker] оборот «{name}» сорвался целиком: "
                  f"{type(e).__name__}: {str(e)[:120]}", flush=True)
        # Событие подтверждается ТОЛЬКО после того, как шаг отработал.
        if _PENDING_EVENT[0] is not None:
            try:
                from core import events
                if ran is True:
                    events.complete(_PENDING_EVENT[0], f"{name}: отработано")
                else:
                    events.release(_PENDING_EVENT[0])
                    time.sleep(min(interval, 5))
            except Exception:
                pass
            _PENDING_EVENT[0] = None
            continue          # срочное отработано — сразу смотрим, нет ли ещё
        i += 1
        # пропущенный по паузе шаг не стоит полного такта
        time.sleep(interval / len(CYCLE) if ran else interval / len(CYCLE) / 8)


def _turn(name, fn, agent, i):
    """Один оборот: выполнить шаг, записать исход, при повторе увести на паузу.

    Возвращает True, если шаг действительно выполнялся, и False, если он был
    пропущен по паузе — вызывающий по этому решает, ждать полный такт или нет.
    """
    if not should_run(name):
        return False
    started = now()
    try:
        # Замер вокруг шага. Мерится и падение тоже: считать только удачные
        # вызовы значит не увидеть, что время уходит на повторы после отказов.
        with telemetry.span("step", name, agent):
            out = fn()
        paused = note_result(name, out)
        record_run(name, agent, True, str(out), started, now())
        if paused:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] {name}: {out} "
                  f"— то же самое {SAME_LIMIT} раза подряд, пауза на {paused} минут",
                  flush=True)
            say(agent, f"Шаг «{name}» {SAME_LIMIT} раза подряд дал один и тот же "
                       f"результат: «{str(out)[:70]}». Нового он сейчас не приносит, "
                       f"ухожу с ним на паузу на {paused} мин — повторять одно и то же не работа.")
        else:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] {name}: {out}", flush=True)
    except Exception as e:
        detail = f"{type(e).__name__}: {e}"
        record_run(name, agent, False, detail, started, now())
        # ПОВТОРЯЮЩИЙСЯ СБОЙ — ТОЖЕ ПОВТОР. Защита от однообразия смотрела
        # только на успешные ответы, поэтому шаг, падающий с одной и той же
        # ошибкой, бился о неё каждый оборот без передышки: тридцать один раз
        # за восемь минут. Разницы нет: и там, и там система тратит оборот на
        # результат, который уже известен.
        paused_err = note_result(name, detail)
        if paused_err:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] {name}: та же ошибка "
                  f"{SAME_LIMIT} раза подряд, пауза на {paused_err} мин", flush=True)
        say(agent, f"⚠ Шаг «{name}» упал: {detail[:130]}. Записал в журнал, "
                   f"чтобы это не потерялось и попало в мою репутацию.")
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {name} FAILED: {detail}", flush=True)
        return None
    return True


if __name__ == "__main__":
    run_forever(int(sys.argv[1]) if len(sys.argv) > 1 else 90)
