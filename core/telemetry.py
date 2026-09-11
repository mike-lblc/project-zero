"""НАБЛЮДАЕМОСТЬ — замер, которым пользуются САМИ АГЕНТЫ, а не только человек.

Владелец справедливо указал на пробел: измерений не было, деградацию мы
замечали проверками задним числом. Здесь он закрывается — но не так, как это
обычно делают.

ПОЧЕМУ НЕ PROMETHEUS С GRAFANA КАК ОТДЕЛЬНЫЕ СЛУЖБЫ. Им нужны два постоянно
работающих процесса и место на диске под временные ряды. Бюджет системы —
ноль, и каждая новая служба, которую надо поднимать и сторожить, — это то,
что однажды упадёт незамеченным. Мы уже теряли часы на том, что реестр молчал,
а никто не отличал молчание от пустоты.

ЧТО СДЕЛАНО ВМЕСТО. Замеры пишутся туда же, где живёт вся остальная память —
в SQLite, — и отдаются наружу В ФОРМАТЕ PROMETHEUS по адресу /metrics. Это
обычный текст, никаких зависимостей. Если владелец захочет Grafana, она
подключится к этому адресу и ничего менять не придётся: формат тот самый.
А до тех пор измерения уже работают.

ГЛАВНОЕ ОТЛИЧИЕ ОТ ОБЫЧНОЙ ТЕЛЕМЕТРИИ. Эти числа читает не только человек.
Оптимизатору дан инструмент, который смотрит В ТЕ ЖЕ ЗАМЕРЫ и говорит, где
система тратит время впустую: какой шаг стоит дорого и ничего не приносит,
какая модель отвечает медленнее прочих, что падает чаще всего. Замер, который
некому прочитать, — это украшение; замер, на который кто-то действует, — это
наблюдаемость.
"""
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.db import connect, ensure_schema, write  # noqa: E402

SCHEMA = """
CREATE TABLE IF NOT EXISTS spans (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,          -- step | model | source | tool
  name TEXT NOT NULL,
  agent TEXT,
  ms INTEGER NOT NULL,
  ok INTEGER NOT NULL DEFAULT 1,
  detail TEXT,
  at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS spans_kind_name ON spans(kind, name);
CREATE INDEX IF NOT EXISTS spans_at ON spans(at);
"""


def now():
    return datetime.now(timezone.utc).isoformat()


def _con():
    c = connect()
    ensure_schema(c, SCHEMA)
    return c


def record(kind, name, ms, ok=True, agent=None, detail=None):
    """Записывает один замер. Отказ записи не должен ронять измеряемое.

    Телеметрия не имеет права быть причиной сбоя: измерительный прибор,
    ломающий станок, хуже отсутствия прибора.
    """
    try:
        c = _con()
        write(c, "INSERT INTO spans(kind,name,agent,ms,ok,detail,at) VALUES (?,?,?,?,?,?,?)",
              (kind, name, agent, int(ms), 1 if ok else 0,
               (detail or "")[:200], now()))
        c.commit(); c.close()
    except Exception:
        pass


@contextmanager
def span(kind, name, agent=None):
    """Замер вокруг куска работы. Падение измеряется тоже — оно и есть главное.

    Считать только успешные вызовы значит не увидеть, что половина времени
    уходит на повторы после отказов.
    """
    t0 = time.time()
    ok, detail = True, None
    try:
        yield
    except Exception as e:
        ok, detail = False, f"{type(e).__name__}: {str(e)[:120]}"
        raise
    finally:
        record(kind, name, (time.time() - t0) * 1000, ok, agent, detail)


# ══════════════════════════════════════════════ ЧТЕНИЕ ЗАМЕРОВ
def summary(hours=24, kind=None):
    """Сводка по замерам: сколько, сколько упало, медиана и худший случай."""
    c = _con()
    sql = ("SELECT kind, name, COUNT(*), SUM(1-ok), "
           "       AVG(ms), MAX(ms) FROM spans "
           "WHERE at > datetime('now', ?) ")
    args = [f"-{int(hours)} hours"]
    if kind:
        sql += "AND kind=? "
        args.append(kind)
    sql += "GROUP BY kind, name ORDER BY COUNT(*)*AVG(ms) DESC"
    rows = c.execute(sql, args).fetchall()
    c.close()
    return [{"kind": r[0], "name": r[1], "calls": r[2], "failed": r[3] or 0,
             "avg_ms": round(r[4] or 0), "max_ms": round(r[5] or 0),
             "total_s": round((r[2] * (r[4] or 0)) / 1000, 1)} for r in rows]


def where_time_goes(hours=24, top=8):
    """Куда уходит время системы. Это и есть вопрос, ради которого всё мерится."""
    rows = summary(hours)
    if not rows:
        return "замеров пока нет — телеметрия только что включена"
    spent = sum(r["total_s"] for r in rows)
    worst = [r for r in rows if r["failed"]][:3]
    head = ", ".join(f"{r['name']} {r['total_s']}с ({r['calls']}×)" for r in rows[:top])
    out = f"за {hours} ч потрачено {round(spent)}с; дороже всего: {head}"
    if worst:
        out += "; ПАДАЕТ: " + ", ".join(
            f"{r['name']} {r['failed']} из {r['calls']}" for r in worst)
    return out


# ФОРМАТ PROMETHEUS ОТДАЁТ СЛУЖБА, а не этот модуль. Здесь была вторая
# реализация тех же строк — и она никем не вызывалась. Две реализации одной
# выдачи расходятся всегда: сначала по мелочи, потом по существу. Адрес один:
# GET /metrics у службы на :8402.

if __name__ == "__main__":
    print("=" * 72)
    print("НАБЛЮДАЕМОСТЬ — куда уходит время системы")
    print("=" * 72)
    rows = summary(24)
    if not rows:
        print("\nзамеров нет: телеметрия включена, но цикл ещё не прошёл")
    else:
        print(f"\n{'ВИД':<8}{'ОТРЕЗОК':<24}{'ВЫЗОВОВ':>8}{'УПАЛО':>7}"
              f"{'СРЕДНЕЕ':>9}{'ХУДШЕЕ':>9}{'ВСЕГО':>9}")
        for r in rows[:25]:
            print(f"{r['kind']:<8}{r['name'][:23]:<24}{r['calls']:>8}{r['failed']:>7}"
                  f"{r['avg_ms']:>8}мс{r['max_ms']:>8}мс{r['total_s']:>8}с")
    print("\n" + where_time_goes())
