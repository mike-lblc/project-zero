"""АГЕНТЫ РАЗВИТИЯ — ищут новое, чинят остальных, улучшают систему.

Три роли:
  EXPLORER  — не даёт залипнуть в одной идее: ищет незанятые ниши и другие пути
  CRITIC    — проверяет работу остальных агентов и находит гниль
  OPTIMIZER — измеряет саму систему и предлагает улучшения

ВАЖНО: они НАХОДЯТ и ПРЕДЛАГАЮТ. Менять код и принимать решения им нельзя —
это делает совет через гейты (DECISION_PROTOCOL.md). Агент, который правит
других без проверки, деградирует систему, а не развивает её.
"""
import sys, json, statistics
from pathlib import Path
from datetime import datetime, timezone, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.db import connect
from core import guard

ROOT = Path(__file__).resolve().parent.parent
IDX = ROOT / "data" / "bazaar_index.json"


def now():
    return datetime.now(timezone.utc).isoformat()


def say(agent, text):
    con = connect()
    con.execute("INSERT INTO messages(sender,recipient,topic,body,created_at) VALUES (?,?,?,?,?)",
                (agent, None, "chat", text, now()))
    con.commit(); con.close()


def note(agent, claim, conf=None):
    con = connect()
    sid = con.execute("INSERT INTO sources(url,title,fetched_at,raw_excerpt) VALUES (?,?,?,?)",
                      (f"agent://{agent}", f"{agent} finding", now(), claim[:400])).lastrowid
    con.execute("INSERT INTO evidence(claim,source_id,agent,created_at,confidence) VALUES (?,?,?,?,?)",
                (claim, sid, agent, now(), conf))
    con.commit(); con.close()


def propose(summary, falsifier, action_class="GREEN", agent="explorer"):
    """Предложение обязано содержать фальсификатор — как понять, что мы ошиблись."""
    con = connect()
    pid = con.execute("INSERT INTO proposals(action_class,summary,payload,falsifier,evidence_ids,"
                      "agent,created_at) VALUES (?,?,?,?,?,?,?)",
                      (action_class, summary, "{}", falsifier, "[]", agent, now())).lastrowid
    con.commit(); con.close()
    return pid


def _index():
    try:
        return json.loads(IDX.read_text(encoding="utf-8"))
    except Exception:
        return []


# ============================================================ EXPLORER
def explore():
    """Ищет НЕЗАНЯТЫЕ ниши: где спрос есть, а предложения мало.

    Считает по живым данным: для каждого тега — сколько сервисов его занимают
    и сколько уникальных плательщиков там крутится. Хорошая возможность = много
    плательщиков на МАЛО поставщиков. Это арифметика, а не фантазия.
    """
    guard.check_action("research", "GREEN")
    items = _index()
    if not items:
        return "индекса нет"
    stat = {}
    for it in items:
        q = it.get("quality") or {}
        payers = q.get("l30DaysUniquePayers") or 0
        calls = q.get("l30DaysTotalCalls") or 0
        for t in (it.get("tags") or []):
            d = stat.setdefault(t, {"n": 0, "payers": 0, "calls": 0})
            d["n"] += 1; d["payers"] += payers; d["calls"] += calls
    # спрос на одного поставщика — чем выше, тем свободнее ниша
    ranked = []
    for t, d in stat.items():
        if d["n"] < 3 or d["payers"] < 10:
            continue                      # слишком мало данных, чтобы верить
        ranked.append((d["payers"] / d["n"], t, d))
    ranked.sort(reverse=True)
    if not ranked:
        return "недостаточно данных для выводов"
    top = ranked[:5]
    txt = "; ".join(f"{t}: {d['payers']} плательщиков на {d['n']} сервисов "
                    f"({ratio:.1f} на одного)" for ratio, t, d in top)
    say("explorer", f"Искал, где спрос выше конкуренции. Самые свободные ниши сейчас — {txt}. "
                    f"Это не значит «идём туда»: сначала надо проверить, чем там платят и "
                    f"сможем ли мы сделать это бесплатно.")
    note("explorer", f"OPPORTUNITY SCAN: demand-per-provider leaders — {txt}", conf=0.75)
    return f"просканировано {len(stat)} категорий, топ: {top[0][1]}"


def explore_alternatives():
    """Не даёт зациклиться на x402: перечисляет другие пути и честно их взвешивает."""
    paths = [
        ("x402-эндпоинт", "рельс подтверждён, оплата работает", "нас никто не находит"),
        ("email-рассылка", "аккаунт есть, opt-in работает", "нужна аудитория, это недели"),
        ("открытые данные как приманка", "у нас краул 14k сервисов — этого нет ни у кого",
         "бесплатное не приносит денег напрямую"),
        ("MCP-инструмент", "тот же индекс, другая витрина, агенты уже ищут MCP",
         "не проверено, есть ли там оплата"),
    ]
    lines = " | ".join(f"{n}: + {p}, − {m}" for n, p, m in paths)
    say("explorer", f"Напоминаю: путь не один. {lines}. Сейчас узкое место одно — ДИСТРИБУЦИЯ, "
                    f"и оно общее для всех вариантов, кроме открытых данных.")
    note("explorer", f"PATH REVIEW (anti-tunnel-vision): {lines}", conf=0.7)
    return "пути пересмотрены"


# ============================================================ CRITIC
def critique():
    """Проверяет работу ОСТАЛЬНЫХ агентов и ищет гниль: утверждения без источника,
    протухшие выводы, противоречия, брошенные предложения."""
    guard.check_action("research", "GREEN")
    con = connect()
    q = lambda s, *a: con.execute(s, a).fetchone()[0]
    problems = []

    orphan = q("SELECT COUNT(*) FROM evidence e LEFT JOIN sources s ON s.id=e.source_id "
               "WHERE s.id IS NULL")
    if orphan:
        problems.append(f"{orphan} утверждений ссылаются на несуществующий источник")

    nofals = q("SELECT COUNT(*) FROM proposals WHERE falsifier IS NULL OR trim(falsifier)=''")
    if nofals:
        problems.append(f"{nofals} предложений без фальсификатора")

    stale_days = 3
    cutoff = (datetime.now(timezone.utc) - timedelta(days=stale_days)).isoformat()
    stale = q("SELECT COUNT(*) FROM proposals WHERE status='proposed' AND created_at < ?", cutoff)
    if stale:
        problems.append(f"{stale} предложений висят без решения дольше {stale_days} дней")

    unjudged = q("SELECT COUNT(*) FROM proposals p WHERE NOT EXISTS "
                 "(SELECT 1 FROM rulings r WHERE r.proposal_id=p.id)")
    if unjudged:
        problems.append(f"{unjudged} предложений вообще без разбора судьёй")

    lowconf = q("SELECT COUNT(*) FROM evidence WHERE confidence IS NULL")
    if lowconf:
        problems.append(f"{lowconf} утверждений без оценки уверенности")
    con.close()

    if not problems:
        say("critic", "Проверил работу остальных: сирот, предложений без фальсификатора и "
                      "брошенных решений нет. Чисто.")
        return "замечаний нет"
    txt = "; ".join(problems)
    say("critic", f"Нашёл слабые места в нашей же работе: {txt}. Это не катастрофа, но если не "
                  f"чинить — доказательство начнёт протухать изнутри.")
    note("critic", f"SELF-AUDIT FINDINGS: {txt}", conf=0.9)
    return f"найдено проблем: {len(problems)}"


# ============================================================ OPTIMIZER
def optimize():
    """Измеряет саму систему и предлагает конкретные улучшения, а не лозунги."""
    guard.check_action("research", "GREEN")
    con = connect()
    rows = con.execute("SELECT result, created_at FROM actions WHERE kind='health_check' "
                       "ORDER BY id DESC LIMIT 40").fetchall()
    total = len(rows)
    bad = sum(1 for r in rows if r[0] != "healthy")
    ev = con.execute("SELECT COUNT(*) FROM evidence").fetchone()[0]
    src = con.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
    msgs = con.execute("SELECT COUNT(*) FROM messages WHERE topic='chat'").fetchone()[0]
    con.close()

    uptime = 100.0 if not total else round(100 * (total - bad) / total, 1)
    ratio = round(ev / src, 2) if src else 0
    items = _index()
    findings = [f"аптайм сервиса по {total} проверкам: {uptime}%",
                f"утверждений на источник: {ratio}",
                f"размер индекса: {len(items)}",
                f"реплик в чате: {msgs}"]

    suggestions = []
    if uptime < 99 and total >= 5:
        suggestions.append("аптайм ниже 99% — туннель ненадёжен, нужен постоянный хостинг")
    if ratio > 3:
        suggestions.append("слишком много выводов на один источник — рискуем накручивать себя")
    if len(items) < 14000:
        suggestions.append("индекс неполный — краул стоит догнать")
    if not suggestions:
        suggestions.append("узких мест в системе не вижу; ограничение снаружи, а не внутри")

    say("optimizer", f"Померил систему: {', '.join(findings)}. Что предлагаю: "
                     f"{'; '.join(suggestions)}.")
    note("optimizer", f"SYSTEM METRICS: {', '.join(findings)} | SUGGESTIONS: {'; '.join(suggestions)}",
         conf=0.85)
    return f"аптайм {uptime}%, предложений: {len(suggestions)}"


# ============================================================ CRITIC: РЕМОНТ
# Критик чинит САМ только то, что безопасно и обратимо: гигиену данных.
# Код, цены, стратегия, удаление данных - ТОЛЬКО через совет. Иначе это дрейф.
SAFE_TO_FIX = {"missing_confidence"}


def repair():
    """Чинит безопасные находки Критика. Каждая правка попадает в журнал."""
    guard.check_action("research", "GREEN")
    con = connect()
    fixed = []

    # утверждения без оценки уверенности -> ставим консервативную 0.5
    # (не 0.9: мы не перепроверяли их заново, значит завышать нельзя)
    rows = con.execute("SELECT id, agent FROM evidence WHERE confidence IS NULL").fetchall()
    for rid, agent in rows:
        con.execute("UPDATE evidence SET confidence=0.5 WHERE id=?", (rid,))
        con.execute("INSERT INTO actions(kind,action_class,dry_run,payload,result,created_at) "
                    "VALUES (?,?,?,?,?,?)",
                    ("repair_missing_confidence", "GREEN", 0,
                     f"evidence #{rid} (agent={agent})",
                     "confidence=0.5 (консервативно: не перепроверялось)", now()))
        fixed.append(f"находка #{rid}")
    con.commit()

    # то, что чинить САМИМ нельзя - только доложить
    unsafe = []
    n = lambda q: con.execute(q).fetchone()[0]
    orph = n("SELECT COUNT(*) FROM evidence e LEFT JOIN sources s ON s.id=e.source_id "
             "WHERE s.id IS NULL")
    if orph:
        unsafe.append(f"{orph} утверждений без источника (удалять данные самим нельзя)")
    stale = n("SELECT COUNT(*) FROM proposals WHERE status='proposed'")
    if stale:
        unsafe.append(f"{stale} предложений ждут решения владельца")
    con.close()

    if fixed:
        say("critic", f"Починил сам: {', '.join(fixed)} — проставил осторожную оценку 0.5, "
                      f"потому что заново их не перепроверял. Каждая правка в журнале.")
        note("critic", f"AUTO-REPAIR: {len(fixed)} evidence rows given conservative confidence=0.5. "
                       f"Logged in actions table.", conf=1.0)
    if unsafe:
        say("critic", f"Сам НЕ трогаю: {'; '.join(unsafe)}. Это не гигиена данных — "
                      f"тут нужен совет или владелец.")
    if not fixed and not unsafe:
        say("critic", "Чинить нечего — данные чистые.")
    return f"починено {len(fixed)}, вне моих полномочий {len(unsafe)}"


CYCLE = [("explore", explore), ("critique", critique), ("repair", repair),
         ("optimize", optimize), ("explore_alternatives", explore_alternatives)]


if __name__ == "__main__":
    for name, fn in CYCLE:
        try:
            print(f"{name}: {fn()}")
        except Exception as e:
            print(f"{name} FAILED: {type(e).__name__}: {e}")
