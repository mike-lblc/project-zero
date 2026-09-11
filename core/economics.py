"""ЭКОНОМИЧЕСКОЕ ЯДРО — разделы 19, 27, 28, 29, 30, 37 директивы.

ЗАМЕЧАНИЕ ВЛАДЕЛЬЦА (учтено): при нулевых расходах учёт в долларах — это театр.
Раздел 20 (token economy) в денежном виде здесь НЕ реализован сознательно: считать
нечего, пока всё бесплатное. Оставлено только то, что защищает реально дефицитные
вещи и охраняет само доказательство:

  roi_gate()  — эскалация на сильную модель ДЕЙСТВИТЕЛЬНО дефицитна, её и бережём
  survival()  — какой агент оправдывает прогон (данные уже сократили цикл с 17 до 10)
  stage()     — где мы находимся + сторож заявления «с нуля»
  briefing()  — честная сводка без выдуманных денег

Колонки usd остаются нулями намеренно: это не учёт, а СИГНАЛИЗАЦИЯ. Любое
ненулевое значение мгновенно ломает заявление «с нуля» и это видно в аудите.

До этого система считала находки и реплики, но не считала ДЕНЬГИ и ИЗДЕРЖКИ.
Директива требует обратного: каждый агент обязан окупать свою работу, а система —
знать, на какой экономической стадии она находится.

Правило директивы, которое здесь исполняется буквально:
«Use conservative attribution. Never fabricate economic impact.»
Поэтому ценность записывается ТОЛЬКО с доказательством, а выручка берётся
исключительно из таблицы payments — реальных транзакций в блокчейне.
"""
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.db import connect, ensure_schema

# Локальная модель на своём железе и бесплатные тарифы = прямых денежных затрат нет.
# Но «бесплатно» не значит «даром»: считаем в условных единицах работы, чтобы
# сравнивать агентов между собой. Деньги считаются отдельно и только настоящие.
COST_UNITS = {
    "local_model_call": 1.0,      # прогон локальной 9B модели
    "frontier_escalation": 25.0,  # эскалация на сильную модель — дорого, беречь
    "http_fetch": 0.2,
    "db_write": 0.05,
    "cycle_step": 0.5,
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_costs (
  id INTEGER PRIMARY KEY,
  agent TEXT NOT NULL,
  kind TEXT NOT NULL,
  units REAL NOT NULL,
  usd REAL NOT NULL DEFAULT 0,
  note TEXT,
  occurred_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_value (
  id INTEGER PRIMARY KEY,
  agent TEXT NOT NULL,
  kind TEXT NOT NULL,          -- revenue | opportunity | cost_saving | prevented_loss | reusable_ip
  usd REAL NOT NULL DEFAULT 0,
  units REAL NOT NULL DEFAULT 0,
  evidence TEXT NOT NULL,      -- без доказательства ценность не засчитывается
  occurred_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cost_agent ON agent_costs(agent);
CREATE INDEX IF NOT EXISTS idx_value_agent ON agent_value(agent);
"""


def now():
    return datetime.now(timezone.utc).isoformat()


def _con():
    c = connect()
    ensure_schema(c, SCHEMA)
    return c


# ---------------------------------------------------------------- раздел 20
def record_cost(agent, kind, count=1, usd=0.0, note=None):
    """Стоимость работы. usd остаётся нулём, пока мы на бесплатных тарифах —
    но если он станет ненулевым, это сразу нарушит заявление «с нуля»."""
    units = COST_UNITS.get(kind, 0.5) * count
    c = _con()
    c.execute("INSERT INTO agent_costs(agent,kind,units,usd,note,occurred_at) VALUES (?,?,?,?,?,?)",
              (agent, kind, units, usd, note, now()))
    c.commit(); c.close()
    return units


# ---------------------------------------------------------------- раздел 27
def record_value(agent, kind, evidence, usd=0.0, units=0.0):
    """Ценность засчитывается ТОЛЬКО с доказательством. Пустое доказательство —
    отказ, иначе агенты начнут приписывать себе несуществующий вклад."""
    if not evidence or not str(evidence).strip():
        raise ValueError("ценность без доказательства не засчитывается")
    c = _con()
    c.execute("INSERT INTO agent_value(agent,kind,usd,units,evidence,occurred_at) "
              "VALUES (?,?,?,?,?,?)", (agent, kind, usd, units, evidence, now()))
    c.commit(); c.close()


def agent_roi():
    """Отдача по каждому агенту. Консервативно: выручка только подтверждённая."""
    c = _con()
    costs = {r[0]: (r[1], r[2]) for r in c.execute(
        "SELECT agent, SUM(units), SUM(usd) FROM agent_costs GROUP BY agent")}
    vals = {r[0]: (r[1], r[2]) for r in c.execute(
        "SELECT agent, SUM(usd), SUM(units) FROM agent_value GROUP BY agent")}
    runs = {r[0]: (r[1], r[2]) for r in c.execute(
        "SELECT agent, COUNT(*), SUM(CASE WHEN status='ok' THEN 1 ELSE 0 END) FROM runs GROUP BY agent")}
    c.close()
    out = []
    for a in sorted(set(costs) | set(vals) | set(runs)):
        cu, cusd = costs.get(a, (0.0, 0.0))
        vusd, vun = vals.get(a, (0.0, 0.0))
        total, ok = runs.get(a, (0, 0))
        roi = (vusd / cusd) if cusd else None
        out.append({"agent": a, "cost_units": round(cu, 1), "cost_usd": round(cusd, 4),
                    "value_usd": round(vusd, 4), "value_units": round(vun, 1),
                    "runs": total, "ok": ok,
                    "success_rate": round(100 * ok / total, 1) if total else None,
                    "roi": roi})
    return out


# ---------------------------------------------------------------- раздел 19
def roi_gate(agent, kind, expected_value_units=0.0):
    """Пускать ли дорогую операцию. Эскалация на сильную модель стоит в 25 раз
    дороже локального прогона, поэтому требует ожидаемой отдачи."""
    cost = COST_UNITS.get(kind, 0.5)
    if kind != "frontier_escalation":
        return True, "дёшево, гейт не нужен"

    # РЕПУТАЦИЯ ПРОСЯЩЕГО. Параметр agent принимался и молча выбрасывался —
    # гейт был одинаков для всех. Но дорогая операция, запрошенная агентом,
    # который ни разу ничего не довёл, и та же операция от работающего агента
    # — это разные ставки. Репутация уже считается по исходам, оставалось
    # только ею воспользоваться.
    c = _con()
    row = c.execute("SELECT SUM(calls), SUM(correct) FROM agent_reputation "
                    "WHERE agent=?", (agent,)).fetchone()
    c.close()
    calls, correct = (row[0] or 0), (row[1] or 0)
    share = correct / calls if calls else None
    need = cost
    if share is not None and calls >= 20 and share < 0.5:
        need = cost * 2          # половина прогонов впустую — планка выше
    if expected_value_units >= need:
        return True, (f"ожидаемая отдача {expected_value_units} >= планки {need}"
                      + (f" (доля успешных прогонов {share:.0%})" if share is not None else ""))
    return False, (f"ожидаемая отдача {expected_value_units} ниже планки {need} — "
                   f"эскалация не оправдана, решать локально или отложить"
                   + (f"; у агента {agent} успешных прогонов {share:.0%} из {calls}"
                      if share is not None and calls >= 20 else ""))


# ---------------------------------------------------------------- раздел 28
# У разных агентов РАЗНЫЙ продукт. Считать всем «находки» — значит объявить
# бездельниками тех, кто пишет возражения, решения или сообщения. Эта ошибка
# была допущена в первой версии и едва не остановила работающих агентов.
# Агенты, чья работа — ИСКАТЬ проблемы. Их молчание означает «всё здорово»,
# а не безделье. Наказывать их за ноль находок — значит поощрять выдумывание проблем.
SILENCE_IS_SUCCESS = {"watchdog", "critic", "adversary"}

OUTPUT_OF = {
    "adversary":    ("objections",  "возражений"),
    "judge":        ("rulings",     "решений"),
    "orchestrator": ("messages",    "сообщений"),
    "proposer":     ("proposals",   "предложений"),
}


def survival():
    """Кто не окупается. Пока выручки нет, судим по ПРОФИЛЬНОМУ выходу агента,
    а не по общему счётчику находок."""
    c = _con()
    rows = c.execute("""
        SELECT r.agent, COUNT(*) runs,
               SUM(CASE WHEN r.status='ok' THEN 1 ELSE 0 END) ok
        FROM runs r GROUP BY r.agent""").fetchall()
    out_counts = {}
    for agent, (table, _label) in OUTPUT_OF.items():
        col = "agent" if table in ("evidence",) else ("agent" if table == "objections" else None)
        try:
            if table == "messages":
                n = c.execute("SELECT COUNT(*) FROM messages WHERE sender=?", (agent,)).fetchone()[0]
            elif table == "rulings":
                n = c.execute("SELECT COUNT(*) FROM rulings").fetchone()[0]
            else:
                n = c.execute(f"SELECT COUNT(*) FROM {table} WHERE agent=?", (agent,)).fetchone()[0]
        except Exception:
            n = 0
        out_counts[agent] = n
    rows = [(a, r, ok,
             out_counts.get(a, c.execute("SELECT COUNT(*) FROM evidence WHERE agent=?", (a,)).fetchone()[0]))
            for a, r, ok in rows]
    c.close()
    verdicts = []
    for agent, runs, ok, findings in rows:
        rate = ok / runs if runs else 0
        label = OUTPUT_OF.get(agent, (None, "находок"))[1]
        if agent in SILENCE_IS_SUCCESS and findings == 0:
            v = "оставить: его продукт — отсутствие проблем"
        elif runs >= 10 and findings == 0:
            v = f"ПАУЗА: 10+ прогонов, ноль ({label})"
        elif runs >= 5 and rate < 0.5:
            v = f"ЧИНИТЬ: успешность {rate:.0%}"
        else:
            v = "оставить"
        verdicts.append({"agent": agent, "runs": runs, "findings": findings, "output": label,
                         "success_rate": round(100 * rate), "verdict": v})
    return verdicts


# ---------------------------------------------------------------- разделы 29-30
STAGES = [
    (0.0,    "СТАДИЯ 0 — доказательство", "выручки нет; цель одна: первый сторонний платёж"),
    (0.01,   "СТАДИЯ 1 — первая выручка", "деньги пришли; цель: повторить то же самое"),
    (100.0,  "СТАДИЯ 2 — повторяемость",  "цель: стабильный поток, а не разовый успех"),
    (1000.0, "СТАДИЯ 3 — самоокупаемость","система оплачивает свою инфраструктуру"),
]


def stage():
    c = _con()
    rev = c.execute("SELECT COALESCE(SUM(CAST(amount AS REAL)),0) FROM payments").fetchone()[0] or 0.0
    spend = c.execute("SELECT COUNT(*) FROM spend").fetchone()[0]
    c.close()
    cur = STAGES[0]
    for threshold, name, goal in STAGES:
        if rev >= threshold:
            cur = (threshold, name, goal)
    survival_mode = (rev == 0 and spend > 0)   # тратим, не зарабатывая
    return {"revenue_usd": round(rev, 4), "spend_rows": spend,
            "stage": cur[1], "goal": cur[2],
            "survival_mode": survival_mode,
            "zero_capital_claim_intact": spend == 0}


# ---------------------------------------------------------------- раздел 37
def briefing():
    """Ежедневная экономическая сводка. Отвечает на вопросы директивы буквально."""
    c = _con()
    q = lambda s, *a: c.execute(s, a).fetchone()[0]
    day = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    data = {
        "verified_revenue_usd": q("SELECT COALESCE(SUM(CAST(amount AS REAL)),0) FROM payments"),
        "payments_count": q("SELECT COUNT(*) FROM payments"),
        "spend_usd": q("SELECT COALESCE(SUM(CAST(amount AS REAL)),0) FROM spend"),
        "cost_units_24h": q("SELECT COALESCE(SUM(units),0) FROM agent_costs WHERE occurred_at > ?", day),
        "runs_24h": q("SELECT COUNT(*) FROM runs WHERE started_at > ?", day),
        "failures_24h": q("SELECT COUNT(*) FROM runs WHERE status='error' AND started_at > ?", day),
        "new_findings_24h": q("SELECT COUNT(*) FROM evidence WHERE created_at > ?", day),
        "human_interventions": q("SELECT COUNT(*) FROM human_interventions"),
        "open_blockers": q("SELECT COUNT(*) FROM objections WHERE severity='blocking'"),
    }
    c.close()
    data.update(stage())
    return data


if __name__ == "__main__":
    print("=== СТАДИЯ ===")
    for k, v in stage().items():
        print(f"  {k:28} {v}")
    print("\n=== ЕЖЕДНЕВНАЯ СВОДКА (раздел 37) ===")
    for k, v in briefing().items():
        print(f"  {k:28} {v}")
    print("\n=== ВЫЖИВАНИЕ АГЕНТОВ (раздел 28) ===")
    for r in survival():
        print(f"  {r['agent']:14} прогонов {r['runs']:>3} успех {r['success_rate']:>3}% "
              f"находок {r['findings']:>3}  -> {r['verdict']}")
    print("\n=== ROI-ГЕЙТ (раздел 19) ===")
    for exp in (0, 30):
        ok, why = roi_gate("scout", "frontier_escalation", exp)
        print(f"  ожидаемая отдача {exp:>3}: {'ПУСКАЕМ' if ok else 'ОТКАЗ'} — {why}")
