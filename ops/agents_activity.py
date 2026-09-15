"""АКТИВНОСТЬ АГЕНТОВ — кто работает, а кто только числится.

Владелец 15.09.2026: «перепроверить всех агентов по последней активности и
составить таблицу: кто действительно работает, а кто просто существует и не
делает никаких усилий». Таблица считается из базы, не из самоотчётов:

  прогоны      — runs (сколько оборотов получил, сколько удачных, сколько разных исходов);
  усилие       — что оставило след вне цикла: находки (evidence), собственные решения
                 (agent_decisions), реплики в чат без служебного шума (messages),
                 внешние действия (outreach, dealer_attempts с отправленным сообщением,
                 moltbook_receipts, code_fixes, pull_requests, proof_of_work);
  вердикт      — РАБОТАЕТ: внешнее действие или новая находка/решение за сутки;
                 ВХОЛОСТУЮ: обороты есть, следа нет, исходы повторяются;
                 МОЛЧИТ: за сутки оборотов нет, за неделю были;
                 ТОЛЬКО СУЩЕСТВУЕТ: за неделю ни оборота, ни следа.

    py -3.13 -X utf8 ops/agents_activity.py            # таблица в stdout (markdown)
    py -3.13 -X utf8 ops/agents_activity.py --json     # то же в JSON
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.db import connect  # noqa: E402

NOISE = re.compile(r"молчат дольше|ухожу с ним на паузу|то же самое|один и тот же результат|WATCHDOG:|PENDING JUDGMENT|"
                   r"Проверяю поступления|Иду за свежим срезом|light probe only", re.I)

# Внешние действия, у которых нет колонки agent: владелец известен по шагу.
OWNED = {
    "outreach": ("salesman", "SELECT COUNT(*) FROM outreach WHERE sent_at > ?"),
    "dealer_attempts": ("dealer", "SELECT COUNT(*) FROM dealer_attempts WHERE message_ref IS NOT NULL AND created_at > ?"),
    "code_fixes": ("improver", "SELECT COUNT(*) FROM code_fixes WHERE at > ?"),
    "pull_requests": ("craftsman", "SELECT COUNT(*) FROM pull_requests WHERE created_at > ?"),
    "proof_of_work": ("craftsman", "SELECT COUNT(*) FROM proof_of_work WHERE created_at > ?"),
}


def _since(c, hours):
    return c.execute("SELECT strftime('%Y-%m-%dT%H:%M:%S','now',?)", (f"-{hours} hours",)).fetchone()[0]


def _agents():
    from core import roster
    from agents import worker
    llm = set(roster.wire())
    mechanical = set(worker.AGENT_OF.values()) - llm
    return sorted(llm), sorted(mechanical)


def _one(c, t24, t7d, name):
    row = {"agent": name}
    for label, since in (("24h", t24), ("7d", t7d)):
        runs = c.execute("SELECT COUNT(*), SUM(status='ok'), COUNT(DISTINCT substr(notes,1,80)) FROM runs "
                         "WHERE agent=? AND started_at > ?", (name, since)).fetchone()
        row[f"runs_{label}"] = runs[0] or 0
        row[f"ok_{label}"] = runs[1] or 0
        row[f"distinct_{label}"] = runs[2] or 0
        found = c.execute("SELECT claim FROM evidence WHERE agent=? AND created_at > ?", (name, since)).fetchall()
        row[f"findings_{label}"] = sum(1 for (cl,) in found if not NOISE.search(cl or ""))
        row[f"decisions_{label}"] = c.execute("SELECT COUNT(*) FROM agent_decisions WHERE agent=? AND ok=1 AND decided_at > ?",
                                              (name, since)).fetchone()[0]
        said = c.execute("SELECT body FROM messages WHERE sender=? AND topic='chat' AND created_at > ?",
                         (name, since)).fetchall()
        row[f"said_{label}"] = sum(1 for (b,) in said if not NOISE.search(b or ""))
        ext = c.execute("SELECT COUNT(*) FROM moltbook_receipts WHERE agent=? AND state='ok' AND created_at > ?",
                        (name, since)).fetchone()[0]
        for tbl, (owner, sql) in OWNED.items():
            if owner == name:
                try:
                    ext += c.execute(sql, (since,)).fetchone()[0]
                except Exception:
                    pass
        row[f"external_{label}"] = ext
    last = c.execute("SELECT MAX(started_at) FROM runs WHERE agent=?", (name,)).fetchone()[0]
    row["last_run"] = (last or "")[:16].replace("T", " ")
    last_step = c.execute("SELECT substr(notes,1,60) FROM runs WHERE agent=? ORDER BY id DESC LIMIT 1", (name,)).fetchone()
    row["last_step"] = (last_step[0] if last_step else "") or ""
    # вердикт
    # Одно решение рассуждающего за сутки — это очередь дала слово, а не усилие;
    # усилием считается след наружу или новая находка.
    effort7 = row["external_7d"] + row["findings_7d"] + row["said_7d"]
    novelty = (row["distinct_24h"] / row["runs_24h"]) if row["runs_24h"] else 0.0
    row["novelty_24h"] = round(novelty, 2)
    if row["external_24h"] > 0:
        row["verdict"] = "ДЕЙСТВУЕТ"            # оставил след снаружи: обращение, пост, правка, PR
    elif row["findings_24h"] > 0:
        row["verdict"] = "ИЩЕТ"                 # принёс новые находки, наружу не выходил
    elif row["runs_24h"] > 0 and novelty >= 0.3:
        row["verdict"] = "ДЕЖУРИТ"              # обороты с разными исходами, но ни находок, ни действий
    elif row["runs_24h"] > 0:
        row["verdict"] = "ВХОЛОСТУЮ"            # обороты с одним и тем же исходом
    elif row["runs_7d"] > 0 or effort7 > 0:
        row["verdict"] = "МОЛЧИТ"               # за сутки ни оборота
    else:
        row["verdict"] = "ТОЛЬКО СУЩЕСТВУЕТ"    # за неделю ни оборота, ни следа
    return row


def table():
    c = connect()
    t24, t7d = _since(c, 24), _since(c, 24 * 7)
    llm, mech = _agents()
    rows = []
    for name in llm:
        r = _one(c, t24, t7d, name); r["kind"] = "LLM"; rows.append(r)
    for name in mech:
        r = _one(c, t24, t7d, name); r["kind"] = "механический"; rows.append(r)
    c.close()
    order = {"ДЕЙСТВУЕТ": 0, "ИЩЕТ": 1, "ДЕЖУРИТ": 2, "ВХОЛОСТУЮ": 3, "МОЛЧИТ": 4, "ТОЛЬКО СУЩЕСТВУЕТ": 5}
    rows.sort(key=lambda r: (order[r["verdict"]], -(r["external_24h"] + r["findings_24h"]), r["agent"]))
    return rows


def markdown(rows):
    head = ("| агент | тип | вердикт | прогонов 24ч (ок/разных) | находок 24ч | решений 24ч | реплик 24ч | "
            "внешних действий 24ч | 7д: прогонов/находок/внешних | последний прогон | последний шаг |")
    lines = [head, "|" + "---|" * 11]
    for r in rows:
        lines.append(f"| {r['agent']} | {r['kind']} | **{r['verdict']}** | {r['runs_24h']} ({r['ok_24h']}/{r['distinct_24h']}) "
                     f"| {r['findings_24h']} | {r['decisions_24h']} | {r['said_24h']} | {r['external_24h']} "
                     f"| {r['runs_7d']}/{r['findings_7d']}/{r['external_7d']} | {r['last_run']} | {r['last_step'][:50]} |")
    return "\n".join(lines)


if __name__ == "__main__":
    rows = table()
    if "--json" in sys.argv:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
    else:
        print(markdown(rows))
        from collections import Counter
        print("\nитого:", dict(Counter(r["verdict"] for r in rows)))
