"""ОБЩАЯ ДОСКА — то, что видит КАЖДЫЙ агент перед тем, как решать.

Требование владельца 15.09.2026: мышление — это не отдельный агент, а агенты,
которые общаются между собой; рассуждающие думают вслух, остальные видят это,
учатся и дополняют друг друга. До этого каждый агент при решении видел только
своё: свои прогоны, свои задачи, свои входящие. Другие агенты существовали для
него как имена в справочнике.

Доска не новая таблица, а вид на то, что уже пишется в базу: находки других
агентов (evidence), их слова (messages), вопросы и передачи работы, адресованные
этой роли (bus), общий план — гипотезы путей к платежу (strategy_hypotheses) и
нехватки, которые их блокируют, и деньги как они есть (поступления, сделки,
лучшие цели дилера). Всё только из базы, без сети, коротко: доска читается
моделью каждый оборот.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.db import connect  # noqa: E402

# Шум, который на доску не попадает: служебные повторы сторожа и паузы шагов.
NOISE = re.compile(r"молчат дольше|ухожу с ним на паузу|то же самое|один и тот же результат|WATCHDOG:|PENDING JUDGMENT|"
                   r"Проверяю поступления|Иду за свежим срезом|light probe only", re.I)


def _tables(c):
    return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def view(role: str, limit: int = 8) -> dict:
    """Доска для роли: что нашли другие, кто что просит, каков общий план, где деньги."""
    c = connect()
    have = _tables(c)
    board = {}
    try:
        # 1) находки других агентов — уверенные и свежие
        if "evidence" in have:
            rows = c.execute(
                "SELECT agent, substr(claim,1,160), substr(created_at,1,16) FROM evidence "
                "WHERE agent IS NOT NULL AND agent<>? AND COALESCE(confidence,0)>=0.7 "
                "ORDER BY id DESC LIMIT 60", (role,)).fetchall()
            board["находки других"] = [f"{r[0]} ({r[2]}): {r[1]}" for r in rows if not NOISE.search(r[1] or "")][:limit]
        # 2) что другие сказали недавно (без шума)
        if "messages" in have:
            rows = c.execute(
                "SELECT sender, substr(body,1,160) FROM messages WHERE topic='chat' AND sender<>? "
                "AND recipient IS NULL ORDER BY id DESC LIMIT 40", (role,)).fetchall()
            # Не больше двух реплик от одного агента и без почти-повторов (первые 60 знаков):
            # иначе один охотник за наградами занимает всю доску одним и тем же сообщением.
            said, per, seen = [], {}, set()
            for s, b in rows:
                if NOISE.search(b or ""):
                    continue
                key = (s, re.sub(r"\d+", "#", (b or "")[:60]))
                if key in seen or per.get(s, 0) >= 2:
                    continue
                seen.add(key); per[s] = per.get(s, 0) + 1
                said.append(f"{s}: {b}")
                if len(said) >= limit:
                    break
            board["что говорят другие"] = said
        # 3) вопросы и передачи, адресованные мне; ответы на мои вопросы
        # Каждый список отдельно: одна упавшая выборка не должна прятать остальные
        # (так и случилось при первом прогоне: ключ вопроса назывался иначе, и доска
        # молча оставалась без вопросов, передач и ответов для всех агентов).
        from core import bus
        for key, fn in (("вопросы ко мне", lambda: [f"#{q.get('id')} от {q.get('from')}: {q.get('question')}" for q in bus.pending_questions(role)][:5]),
                        ("мне передали", lambda: [f"#{w.get('id')} от {w.get('from')}: {w.get('task')} — {w.get('why')}" for w in bus.my_work(role)][:5]),
                        ("ответы мне", lambda: [f"{a.get('from')}: {a.get('answer')}" for a in bus.answers_for(role, 3)])):
            try:
                board[key] = fn()
            except Exception as e:
                board[key] = [f"(не прочитано: {type(e).__name__})"]
        # 4) общий план: гипотезы путей к платежу и нехватки
        if "strategy_hypotheses" in have:
            rows = c.execute(
                "SELECT name, mechanism, status, score, tries, signal FROM strategy_hypotheses "
                "WHERE status IN ('kept','testing','proposed') ORDER BY score DESC LIMIT 6").fetchall()
            board["общий план (гипотезы)"] = [f"{n} [{m}] {st} score={sc} tries={t} signal={sg}" for n, m, st, sc, t, sg in rows]
            rows = c.execute("SELECT needs, COUNT(*) FROM strategy_hypotheses WHERE status='blocked' GROUP BY needs").fetchall()
            board["чего не хватает"] = [f"{n} гипотез ждут: {needs}" for needs, n in rows]
        # 5) деньги как они есть
        money = {}
        if "payment_receipts" in have:
            money["поступлений"] = c.execute("SELECT COUNT(*) FROM payment_receipts").fetchone()[0]
        if "tasks" in have:
            money["сделок в работе"] = c.execute(
                "SELECT COUNT(*) FROM tasks WHERE kind='deal' AND state IN ('CONTACTED','REPLIED','NEGOTIATING','AGREED','WORKING','QA','DELIVERED','PAYMENT_REQUESTED')").fetchone()[0]
        if "counterparties" in have:
            rows = c.execute("SELECT handle, platform, priority FROM counterparties WHERE status IN ('new','contacted','replied') "
                             "ORDER BY priority DESC LIMIT 3").fetchall()
            money["лучшие цели дилера"] = [f"{h}@{p} ({pr})" for h, p, pr in rows]
        if "channels" in have:
            money["каналов живых"] = c.execute("SELECT COUNT(*) FROM channels WHERE alive=1").fetchone()[0]
        try:
            from core import payment as _pay
            money["принимаем"] = _pay.accepted_line(short=True) + " — любой из этих активов, не только USDC"
        except Exception:
            pass
        board["деньги"] = money
    finally:
        c.close()
    return board


def snapshot(limit_msgs: int = 120, limit_dec: int = 40, limit_find: int = 30) -> dict:
    """Снимок общей доски для живого чата: то, что видят все агенты, — одним JSON.

    Сообщения — чат, вопросы, ответы, передачи (без очереди суждений и без входящих
    снаружи); решения рассуждающих — «думает вслух»; находки — без служебного шума;
    открытые вопросы — кому и сколько; кто чем занят — последний оборот каждого.
    """
    c = connect()
    have = _tables(c)
    out = {"generated_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
           "messages": [], "decisions": [], "findings": [], "pending": [], "agents": []}
    try:
        if "messages" in have:
            rows = c.execute(
                "SELECT id, sender, recipient, topic, substr(body,1,600), created_at FROM messages "
                "WHERE topic IN ('chat','ask','answer','handoff') AND COALESCE(recipient,'')<>'ESCALATION' "
                "ORDER BY id DESC LIMIT ?", (limit_msgs * 2,)).fetchall()
            msgs = []
            for i, snd, rcp, topic, body, at in rows:
                text = body or ""
                if topic in ("ask", "answer", "handoff"):
                    try:
                        j = json.loads(body)
                        text = j.get("q") or j.get("a") or (f"{j.get('task')} — {j.get('why')}" if j.get("task") else body)
                    except Exception:
                        pass
                if topic == "chat" and NOISE.search(text):
                    continue
                msgs.append({"id": i, "from": snd, "to": rcp, "kind": topic, "text": str(text)[:500], "at": at})
                if len(msgs) >= limit_msgs:
                    break
            out["messages"] = msgs[::-1]
        if "agent_decisions" in have:
            out["decisions"] = [
                {"id": i, "agent": a, "tool": t, "why": (w or "")[:300], "ok": bool(ok), "outcome": (o or "")[:200], "at": at}
                for i, a, t, w, ok, o, at in c.execute(
                    "SELECT id, agent, chose, why, ok, outcome, decided_at FROM agent_decisions "
                    "ORDER BY id DESC LIMIT ?", (limit_dec,)).fetchall()][::-1]
        if "evidence" in have:
            rows = c.execute("SELECT id, agent, substr(claim,1,300), created_at FROM evidence "
                             "WHERE agent IS NOT NULL ORDER BY id DESC LIMIT ?", (limit_find * 3,)).fetchall()
            out["findings"] = [{"id": i, "agent": a, "text": cl, "at": at}
                               for i, a, cl, at in rows if not NOISE.search(cl or "")][:limit_find][::-1]
        if "messages" in have:
            out["pending"] = [{"to": r, "n": n} for r, n in c.execute(
                "SELECT recipient, COUNT(*) FROM messages WHERE topic IN ('ask','handoff') AND consumed_at IS NULL "
                "AND recipient IS NOT NULL AND recipient<>'ESCALATION' GROUP BY recipient ORDER BY 2 DESC").fetchall()]
        if "runs" in have:
            out["agents"] = [{"agent": a, "last": at, "step": (n or "")[:120]} for a, at, n in c.execute(
                "SELECT agent, MAX(started_at), notes FROM runs GROUP BY agent ORDER BY 2 DESC").fetchall()]
    finally:
        c.close()
    return out


def brief(role: str) -> str:
    """Та же доска одной строкой — для журнала и отчётов."""
    b = view(role, limit=4)
    return json.dumps(b, ensure_ascii=False)[:900]
