"""КАРТА ВОЗМОЖНОСТЕЙ — общий живой снимок «что подключено и работает».

Владелец спросил прямо: знают ли агенты обо всех подключениях и активны ли
везде. До этого — нет: каждый агент видел только свой белый список инструментов
и рассуждал о возможностях, которых у него нет, или заново приходил к тому, что
система уже знает. Здесь один снимок, который кладётся в состояние КАЖДОГО
агента при решении: какие маршруты оплаты проверены, какие услуги исполнимы,
сколько лидов контактируемо, что в конвейере сделок, что молчит.

Снимок только из базы и только на чтение — никакой сети, чтобы не тормозить
оборот. Он кэшируется на минуту: на цикле из полутора десятков агентов
пересчитывать его на каждый ход незачем, а данные за минуту не устаревают.
"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.db import connect

_CACHE = {"at": 0.0, "map": None}
TTL = 60.0


def _q1(c, sql, default=0):
    try:
        r = c.execute(sql).fetchone()
        return r[0] if r and r[0] is not None else default
    except Exception:
        return default


def _rows(c, sql):
    try:
        return c.execute(sql).fetchall()
    except Exception:
        return []


def build():
    """Считает карту из базы. Быстро, без сети."""
    c = connect()
    try:
        routes = _rows(c, "SELECT currency, network FROM payment_routes WHERE status='verified'")
        pay = sorted({f"{cur}/{net}" for cur, net in routes})
        services = [r[0] for r in _rows(c, "SELECT name FROM services WHERE status='executable'")]
        leads_total = _q1(c, "SELECT COUNT(*) FROM leads")
        leads_ch = _q1(c, "SELECT COUNT(*) FROM leads WHERE reachable=1")
        deals = {st: n for st, n in _rows(c,
                 "SELECT state, COUNT(*) FROM tasks WHERE kind='deal' "
                 "AND state NOT IN ('REJECTED','WITHDRAWN') GROUP BY state")}
        receipts = _q1(c, "SELECT COUNT(*) FROM payment_receipts")
        # что молчит дольше суток — чтобы агенты видели пробелы, а не догадывались
        silent = [r[0] for r in _rows(c,
                  "SELECT DISTINCT agent FROM runs WHERE agent NOT IN "
                  "(SELECT DISTINCT agent FROM runs WHERE started_at > "
                  "strftime('%Y-%m-%dT%H:%M:%S','now','-1 day'))")]
        catalog = _q1(c, "SELECT COUNT(*) FROM money_paths")
    finally:
        c.close()

    return {
        "маршруты оплаты (проверены)": pay or "нет проверенных",
        "принимаем": "любой ликвидный крипто-актив на этих сетях, не только USDC",
        "услуги исполнимы": services,
        "лиды": f"{leads_total} всего, {leads_ch} с публичным каналом (контактируемы)",
        "сделки в работе": deals or "нет",
        "поступлений денег": receipts,
        "площадок в обходе": catalog,
        "молчат дольше суток": silent or "нет",
        "запрещено (не предлагать)": "движение средств, чеканка/продажа NFT, "
                                     "рассылка без согласия, решение проверочных задач",
    }


def capability_map():
    """Карта с кэшем на минуту. Годится для вызова на каждом обороте агента."""
    now = time.time()
    if _CACHE["map"] is None or now - _CACHE["at"] > TTL:
        try:
            _CACHE["map"] = build()
            _CACHE["at"] = now
        except Exception as e:
            return {"карта недоступна": f"{type(e).__name__}"}
    return _CACHE["map"]


if __name__ == "__main__":
    import json
    print(json.dumps(capability_map(), ensure_ascii=False, indent=1))
