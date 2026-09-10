"""НАДСМОТРЩИК — агент, который не даёт системе уходить во внутреннюю работу.

ЧЕСТНО О ТОМ, ЧЕГО ОН НЕ ДЕЛАЕТ: он не «заставляет агентов стараться». Агенты —
это код, они не ленятся. Просить их работать усерднее бессмысленно.

РЕАЛЬНАЯ ПРОБЛЕМА, которую он решает. За первые 13 часов работы система сделала
наружу ДВА действия (сервис на постоянном адресе, запись в реестре MCP), а всё
остальное время измеряла, оценивала и рисовала дашборды сама себе. Внутренняя
работа безопасна и приятна, поэтому система в неё сползает. Денег она не приносит.

Что он делает:
  outward_ratio() — доля действий, ДОСТИГШИХ внешнего мира
  idle_time()     — сколько часов прошло с последнего внешнего действия
  pending_moves() — какие внешние ходы доступны ПРЯМО СЕЙЧАС и не сделаны
  verdict()       — если наружу давно ничего не шло, требует прекратить внутреннюю
                    работу и назвать конкретный ход

Директива, раздел 13: «Internal thought must move toward external economic action
whenever tools and permissions make execution possible.» Этот агент — исполнение
той строки в виде кода, а не пожелания.
"""
import sys, json, urllib.request, urllib.error
from pathlib import Path
from datetime import datetime, timezone, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.db import connect
from core import guard, bus

ROOT = Path(__file__).resolve().parent.parent

# Действие считается ВНЕШНИМ, только если его результат виден за пределами нашей
# машины. Отчёт в базе, находка и реплика в чате — не внешние.
OUTWARD_KINDS = {"publish_service", "publish_mcp_registry", "publish_dataset",
                 "email_send", "public_post", "page_publish", "listing"}

# Ходы, доступные без чужого разрешения. Проверяются фактами, а не памятью.
MOVES = [
    {"id": "mcp_registry", "what": "запись в официальном реестре MCP",
     "check": lambda: _reg_listed(),
     "blocker": None},
    {"id": "pages", "what": "открытый датасет на GitHub Pages",
     "check": lambda: _url_ok("https://mike-lblc.github.io/project-zero/"),
     "blocker": None},
    {"id": "worker", "what": "сервис на постоянном адресе",
     "check": lambda: _url_ok("https://x402-bazaar-rank.x402-bazaar-rank-worker.workers.dev/health"),
     "blocker": None},
    {"id": "bazaar", "what": "листинг в Bazaar Coinbase (14k+ сервисов, живой трафик)",
     "check": lambda: False,
     "blocker": "нужен аккаунт CDP — руки владельца"},
    {"id": "automations", "what": "автоматизации рассылки",
     "check": lambda: _has_sent(),
     "blocker": "API не даёт отправки (405), автоматизации собираются в панели — руки владельца"},
    {"id": "owner_post", "what": "публикация владельцем в сообществе агентов",
     "check": lambda: _draft_published(),
     "blocker": "текст готов в ops/, публикует владелец от своего имени"},
]


def now():
    return datetime.now(timezone.utc)


def _url_ok(u):
    try:
        r = urllib.request.urlopen(urllib.request.Request(
            u, headers={"User-Agent": "P0-taskmaster/0.1"}), timeout=15)
        return r.status < 400
    except Exception:
        return False


def _reg_listed():
    try:
        r = urllib.request.urlopen(urllib.request.Request(
            "https://registry.modelcontextprotocol.io/v0/servers?search=x402-bazaar-rank",
            headers={"User-Agent": "P0-taskmaster/0.1", "Accept": "application/json"}), timeout=20)
        return "x402-bazaar-rank" in r.read().decode()
    except Exception:
        return None          # неизвестно — не врём, что сделано


def _has_sent():
    c = connect()
    n = c.execute("SELECT COUNT(*) FROM email_events WHERE event='sent'").fetchone()[0]
    c.close()
    return n > 0


def _draft_published():
    c = connect()
    try:
        n = c.execute("SELECT COUNT(*) FROM drafts WHERE status='published'").fetchone()[0]
    except Exception:
        n = 0
    c.close()
    return n > 0


# ---------------------------------------------------------------- измерения
def outward_ratio():
    """Сколько нашей работы вообще доходит до внешнего мира."""
    c = connect()
    total = c.execute("SELECT COUNT(*) FROM actions").fetchone()[0]
    marks = ",".join("?" * len(OUTWARD_KINDS))
    out = c.execute(f"SELECT COUNT(*) FROM actions WHERE kind IN ({marks})",
                    tuple(OUTWARD_KINDS)).fetchone()[0]
    runs = c.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    c.close()
    return {"outward_actions": out, "all_actions": total, "agent_runs": runs,
            "ratio_pct": round(100 * out / total, 1) if total else 0.0}


def idle_time():
    """Часы с последнего действия, дошедшего наружу."""
    c = connect()
    marks = ",".join("?" * len(OUTWARD_KINDS))
    last = c.execute(f"SELECT MAX(created_at) FROM actions WHERE kind IN ({marks})",
                     tuple(OUTWARD_KINDS)).fetchone()[0]
    c.close()
    if not last:
        return None
    try:
        return round((now() - datetime.fromisoformat(last)).total_seconds() / 3600, 1)
    except Exception:
        return None


def pending_moves():
    """Какие внешние ходы доступны и не сделаны. Проверка ФАКТАМИ, не памятью."""
    guard.check_action("research", "GREEN")
    done, pending, blocked = [], [], []
    for m in MOVES:
        try:
            state = m["check"]()
        except Exception:
            state = None
        row = {"id": m["id"], "what": m["what"], "blocker": m["blocker"]}
        if state is True:
            done.append(row)
        elif m["blocker"]:
            blocked.append(row)
        else:
            pending.append(row)
    return {"done": done, "pending": pending, "blocked": blocked}


# ---------------------------------------------------------------- вердикт
def verdict():
    """Главный вопрос: мы двигаемся наружу или крутимся внутри?"""
    r = outward_ratio()
    idle = idle_time()
    m = pending_moves()
    c = connect()
    revenue = c.execute("SELECT COUNT(*) FROM payments").fetchone()[0]
    c.close()

    if m["pending"]:
        names = ", ".join(x["what"] for x in m["pending"])
        bus.broadcast("taskmaster",
                      f"СТОП внутренней работе. Доступны и НЕ сделаны внешние ходы: {names}. "
                      f"Пока они лежат, любые измерения и отчёты — это уход от задачи.")
        return {"state": "ХОД ДОСТУПЕН", "action": names, "idle_hours": idle, **r}

    if revenue == 0 and (idle is None or idle > 6):
        blockers = "; ".join(f"{x['what']} — {x['blocker']}" for x in m["blocked"]) or "нет"
        bus.broadcast("taskmaster",
                      f"Наружу ничего не уходило {idle if idle is not None else '—'} ч, выручка ноль. "
                      f"Все доступные ходы сделаны. Осталось только то, что упирается в владельца: "
                      f"{blockers}. Наращивать внутреннюю работу бессмысленно — она не продаётся.")
        return {"state": "УПЁРЛИСЬ В ВЛАДЕЛЬЦА", "blocked": m["blocked"],
                "idle_hours": idle, **r}

    bus.broadcast("taskmaster",
                  f"Внешних действий {r['outward_actions']} из {r['all_actions']} "
                  f"({r['ratio_pct']}%), последнее {idle} ч назад. Движение есть.")
    return {"state": "ДВИЖЕНИЕ ЕСТЬ", "idle_hours": idle, **r}


CYCLE = [("taskmaster", verdict)]


if __name__ == "__main__":
    print("=== ДОЛЯ РАБОТЫ, ДОШЕДШЕЙ НАРУЖУ ===")
    for k, v in outward_ratio().items():
        print(f"  {k:20} {v}")
    print(f"\n  часов с последнего внешнего действия: {idle_time()}")
    print("\n=== ВНЕШНИЕ ХОДЫ ===")
    m = pending_moves()
    for x in m["done"]:
        print(f"  [СДЕЛАНО]  {x['what']}")
    for x in m["pending"]:
        print(f"  [ДОСТУПЕН] {x['what']}")
    for x in m["blocked"]:
        print(f"  [УПЁРЛОСЬ] {x['what']} — {x['blocker']}")
    print("\n=== ВЕРДИКТ ===")
    v = verdict()
    print(f"  {v['state']}")
