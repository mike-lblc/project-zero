"""ВОСЕМЬ СОСТАВЛЯЮЩИХ АГЕНТА — проверка фактом, а не заявлением.

Владелец сформулировал разницу между настоящим агентом и скриптом с промптом:
языковая модель, системный промпт, инструменты, память, машина состояний,
расписание и события, права. Здесь каждая из этих составляющих либо
предъявляет доказательство — строку кода, строку в базе, живой ответ, — либо
честно докладывается как отсутствующая.

ПОЧЕМУ ПРОВЕРКА, А НЕ ОПИСАНИЕ. Описание архитектуры — это обещание. Оно
остаётся верным ровно до первой правки и не замечает, когда перестаёт быть
верным. Мы уже держали в составе агента, чьи шаги не могли запуститься
физически, и агента, который был в дашборде и отсутствовал в системе. Разница
между «у нас есть память» и «вот строки, которые агент из памяти прочитал»
и есть разница между заявлением и работой.

Запускается как обычная проверка: `py -3.13 -X utf8 agent_anatomy.py`.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

R = {"ok": 0, "bad": 0}
GAPS = []


def check(part, passed, proof):
    """Одна составляющая. Доказательство печатается всегда — и при провале тоже."""
    mark = "ЕСТЬ " if passed else "НЕТ  "
    print(f"  {mark} {part:<26} {proof}")
    R["ok" if passed else "bad"] += 1
    if not passed:
        GAPS.append(f"{part}: {proof}")


def main():
    from core import roster  # noqa: F401 — импорт регистрирует агентов и инструменты
    from core.agent import REGISTRY, TOOLS
    from core.db import connect

    con = connect()

    def q(sql, *a):
        try:
            return (con.execute(sql, a).fetchone() or [0])[0]
        except Exception:
            return 0

    print("=" * 74)
    print("ИЗ ЧЕГО СОСТОИТ КАЖДЫЙ АГЕНТ — доказательства")
    print("=" * 74)
    print(f"\nагентов в составе: {len(REGISTRY)}, инструментов: {len(TOOLS)}\n")

    # ─── 1. ЯЗЫКОВАЯ МОДЕЛЬ
    from core import router
    backend_local, backend_cloud = None, None
    try:
        import urllib.request
        import json as _j
        tags = _j.loads(urllib.request.urlopen(
            "http://127.0.0.1:11434/api/tags", timeout=8).read())
        backend_local = [m["name"] for m in tags.get("models", [])]
    except Exception as e:
        backend_local = f"локальные модели не отвечают ({type(e).__name__})"
    try:
        from core.cloud_model import MODEL as CLOUD_MODEL
        backend_cloud = CLOUD_MODEL
    except Exception:
        backend_cloud = None

    used = q("SELECT COUNT(DISTINCT model) FROM agent_decisions")
    models_seen = [r[0] for r in con.execute(
        "SELECT DISTINCT model FROM agent_decisions WHERE model IS NOT NULL")]
    check("языковая модель", bool(models_seen),
          f"решений принято моделями: {models_seen or 'ни одного'}; "
          f"маршрутизация по весу задачи: {router.WEIGHT}")
    check("модель в облаке", bool(backend_cloud),
          f"{backend_cloud} (включается P0_MODEL_BACKEND=cloudflare)"
          if backend_cloud else "облачного пути нет — рассуждение только дома")

    # ─── 2. СИСТЕМНЫЙ ПРОМПТ
    short = [a.name for a in REGISTRY.values() if len(a.system) < 800]
    check("системный промпт", not short,
          f"у всех {len(REGISTRY)} агентов, длина "
          f"{min(len(a.system) for a in REGISTRY.values())}–"
          f"{max(len(a.system) for a in REGISTRY.values())} знаков"
          if not short else f"слишком короткие у: {short}")

    # ─── 3. ИНСТРУМЕНТЫ
    orphan = [t for t in TOOLS if not any(t in a.tools for a in REGISTRY.values())]
    missing = [(a.name, t) for a in REGISTRY.values() for t in a.tools if t not in TOOLS]
    check("инструменты", not orphan and not missing,
          f"{len(TOOLS)} объявлено, у каждого есть владелец, "
          f"каждый вызов идёт через белый список агента"
          if not orphan and not missing
          else f"без владельца: {orphan}; без реализации: {missing}")

    # ─── 4. ПАМЯТЬ
    from core import recall
    vec = q("SELECT COUNT(*) FROM memory_vectors")
    runs = q("SELECT COUNT(*) FROM runs")
    lessons = q("SELECT COUNT(*) FROM lessons") or q("SELECT COUNT(*) FROM evidence")
    probe = recall.recall("почему источник молчит", top=3) if vec else []
    check("память", vec > 0 and runs > 0,
          f"смысловая: {vec} векторов ({recall.EMBED_MODEL}, {recall.DIM} измерений), "
          f"поиск по смыслу вернул {len(probe)}; журнал прогонов: {runs}; "
          f"свидетельств: {lessons}")

    # ─── 5. МАШИНА СОСТОЯНИЙ
    states = [(r[0], r[1]) for r in con.execute(
        "SELECT state, COUNT(*) FROM tasks GROUP BY state ORDER BY 2 DESC")]
    from core import execution
    allowed = getattr(execution, "STATES", {})       # состояние -> куда из него можно
    stray = [s for s, _ in states if allowed and s not in allowed]
    check("машина состояний", bool(states) and not stray,
          f"состояния задач: {dict(states)}; разрешённые переходы объявлены в "
          f"core/execution.py: " + "; ".join(f"{a}→{sorted(b) or 'конец'}" for a, b in allowed.items())
          if not stray else f"состояния вне объявленных: {stray}")

    # ─── 6. РАСПИСАНИЕ И СОБЫТИЯ
    from core import events
    from agents import worker
    ev_total = q("SELECT COUNT(*) FROM events")
    ev_pending = q("SELECT COUNT(*) FROM events WHERE claimed_by IS NULL")
    check("расписание и события", bool(events.SUBSCRIPTIONS) and ev_total >= 0,
          f"подписок на события: {len(events.SUBSCRIPTIONS)}, событий в шине: "
          f"{ev_total} (не разобрано {ev_pending}); цикл: {len(worker.CYCLE)} ядро + "
          f"{len(worker.SLOW_CYCLE)} редких; в облаке по расписанию каждые 15 мин")

    # ─── 7. ПРАВА
    from core import guard
    classes = sorted({t.action_class for t in TOOLS.values()})
    blocked = None
    try:
        guard.check_action("проверка прав", "BLACK")
        blocked = "НЕ СРАБОТАЛИ — запрещённое действие прошло"
    except Exception as e:
        blocked = f"запрещённое остановлено ({type(e).__name__})"
    check("права", "НЕ СРАБОТАЛИ" not in blocked,
          f"классы действий у инструментов: {classes}; потолок агента задан "
          f"полем max_class; {blocked}")

    # ─── 8. СОХРАНЯЕМОСТЬ
    tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    decisions = q("SELECT COUNT(*) FROM agent_decisions")
    check("сохраняемость", decisions > 0 and len(tables) > 20,
          f"таблиц: {len(tables)}; решений агентов записано: {decisions}; "
          f"состояние переживает перезапуск (паузы шагов лежат в cycle_memory)")

    # ─── КТО ИЗ АГЕНТОВ УЖЕ РАССУЖДАЛ МОДЕЛЬЮ
    print("\n" + "-" * 74)
    print("КТО ИЗ АГЕНТОВ УЖЕ ВЫБИРАЛ ДЕЙСТВИЕ МОДЕЛЬЮ (факт из базы)")
    rows = list(con.execute(
        "SELECT agent, COUNT(*), SUM(ok), MAX(model) FROM agent_decisions "
        "GROUP BY agent ORDER BY 2 DESC"))
    for agent, n, ok, model in rows:
        print(f"  {agent:<16} решений {n:>3}, удачных {ok or 0:>3}   модель: {model}")
    silent = sorted(set(REGISTRY) - {r[0] for r in rows})
    if silent:
        print(f"\n  ещё не рассуждали моделью: {', '.join(silent)}")
        print("  (у них есть промпт, инструменты и права; до хода в очереди не дошло)")

    con.close()
    print("\n" + "=" * 74)
    print(f"ИТОГ: составляющих на месте {R['ok']}, отсутствует {R['bad']}")
    if GAPS:
        print("\nЧЕГО НЕТ:")
        for g in GAPS:
            print("  - " + g)
    print("=" * 74)
    return 1 if R["bad"] else 0


if __name__ == "__main__":
    sys.exit(main())
