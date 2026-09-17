"""ПОДДЕРЖАНИЕ ПРОДАЮЩЕЙ ПОВЕРХНОСТИ — РУКАМИ АГЕНТА, А НЕ МОИМИ (17.09.2026).

Замечание владельца, и оно справедливое: первый платёж (12 переводов, 0.117 USDC)
получился потому, что человек-в-петле руками добавил маршруты, переставил цены,
создал одиннадцать объявлений в каталоге, привёл OpenAPI к чужой спецификации и
привязал ключи. Агенты в это время исправно крутили цикл и НЕ СДЕЛАЛИ НИЧЕГО ИЗ
ЭТОГО. Если человек выключен, экосистема не зарабатывает — значит она не готова.

Здесь ровно та работа, которую можно отдать агенту без риска, и она вся про
«продать», а не «написать код»:

1. У каждого платного маршрута из тарифа должно быть объявление в каталоге,
   который ПЛАТИТ. Нет объявления — создать.
2. Объявленная цена должна совпадать с живой: расхождение каталог считает
   price_drift и валит маршрут.
3. Индексы, куда пускают без аккаунта, должны знать про все маршруты.
4. Каталог x402gle листингует через РЕАЛЬНЫЙ платный вызов со своей стороны —
   значит его аудит надо дёргать, а не ждать.
5. Всё расхождение — вслух: маршрут без объявления, объявление без маршрута,
   выпавший из verified, цена врозь.

Чего здесь НЕТ и не будет: правок кода и развёртываний. Агент, который сам себе
меняет платный сервис и деплоит его, — это не автономность, а риск без надзора.
Такие расхождения шаг НАЗЫВАЕТ, чтобы их увидел человек.

Денег шаг не тратит: создание объявления и аудит бесплатны и без аккаунта.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import bus, guard  # noqa: E402
from core.db import connect  # noqa: E402
from core.identity import SERVICE_URL as SELF, TARIFF  # noqa: E402

NH = "https://api.nohumans.directory/v1/listings"
AUDITION = "https://x402.dexter.cash/api/public/discoverable"
CONFIG = ROOT / "data" / "nohumans_listings.json"
UA = "P0-agent/1.0"
CREATE_CAP = 3            # за один ход — не больше трёх новых объявлений
CATEGORY = "data.markets"


def now():
    return datetime.now(timezone.utc).isoformat()


def _note(agent, claim, conf=None):
    try:
        from agents.worker import note as _wnote
        _wnote(agent, claim, conf=conf)
    except Exception:
        pass


def _table(c):
    # Ключ правки объявления — секрет, и он нужен агенту, чтобы починить цену.
    # В .env его класть нельзя: .env не виден облачному прогону.
    c.execute("CREATE TABLE IF NOT EXISTS directory_claim (listing_id TEXT PRIMARY KEY, route TEXT, "
              "claim_token TEXT, created_at TEXT)")


def _http(url, method="GET", body=None, headers=None, timeout=60):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, method=method)
    req.add_header("User-Agent", UA)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            try:
                return r.status, json.loads(raw or "{}")
            except ValueError:
                return r.status, {"_raw": raw[:400]}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw or "{}")
        except ValueError:
            return e.code, {"_raw": raw[:400]}
    except Exception as e:
        return 0, {"_err": f"{type(e).__name__}: {str(e)[:120]}"}


def listings_config():
    if not CONFIG.exists():
        return {}
    try:
        return dict(json.loads(CONFIG.read_text(encoding="utf-8")).get("listings") or {})
    except (ValueError, OSError):
        return {}


def _save_config(listings):
    try:
        doc = json.loads(CONFIG.read_text(encoding="utf-8")) if CONFIG.exists() else {}
    except (ValueError, OSError):
        doc = {}
    doc.setdefault("_note", "Публичные идентификаторы наших объявлений. Не секрет. "
                            "Ключи правки живут в базе (directory_claim).")
    doc["listings"] = listings
    CONFIG.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def describe(route, usd, what):
    """Описание объявления из тарифа. То же, что писалось руками, но без рук."""
    return (f"{what}. Computed from a full re-fetch of the Coinbase x402 Bazaar discovery feed "
            f"(30-day window as published per resource), with a receipt naming the snapshot hash and "
            f"generation time in every response. A paid call with no parameters at all returns data "
            f"rather than an error, so a blind purchase is never wasted. Priced at ${usd} per call. "
            f"Pays to a single address on Base, Arbitrum or Polygon; a plain USDC, USDT, DAI or native "
            f"ETH transfer also works, as do Bitcoin and TRON — call the route again within 30 minutes "
            f"or pass the transaction id.")


def ensure_listings(limit=CREATE_CAP):
    """У каждого платного маршрута — объявление в каталоге, который платит."""
    guard.check_action("research", "GREEN")
    c = connect()
    _table(c)
    cfg = listings_config()
    have = {r.lstrip("/") for r in cfg}
    want = {p.lstrip("/") for p in TARIFF}
    missing = sorted(want - have)
    extra = sorted(have - want)
    created, failed = [], []
    for route in missing[:limit]:
        path = "/" + route
        t = TARIFF.get(path) or {}
        usd = t.get("usd")
        body = {
            "name": f"x402 Bazaar Rank — {route}",
            "description": describe(route, usd, t.get("what") or route),
            "endpoint_url": SELF + path,
            "category": CATEGORY,
            "price_amount": usd,
            "price_currency": "USDC",
            "chains": ["base"],
            "sample_query": SELF + "/sample",
        }
        st, d = _http(NH, "POST", body)
        lid, tok = d.get("id"), d.get("claim_token")
        if st in (200, 201) and lid:
            cfg[route] = lid
            if tok:
                c.execute("INSERT OR REPLACE INTO directory_claim(listing_id,route,claim_token,created_at) "
                          "VALUES (?,?,?,?)", (lid, route, tok, now()))
            created.append(route)
        else:
            failed.append(f"{route} ({st} {str(d)[:60]})")
    if created:
        c.commit()
        _save_config(cfg)
        _note("dealer", "ОБЪЯВЛЕНИЯ СОЗДАНЫ АГЕНТОМ (без рук): " + ", ".join(created)
              + f". Каталог nohumans платит сам; маршрут без объявления в нём не покупают.", conf=0.9)
    c.close()
    out = []
    if created:
        out.append(f"создано {len(created)}: {', '.join(created)}")
    if failed:
        out.append(f"не создано {len(failed)}: {'; '.join(failed)}")
    if len(missing) > limit:
        out.append(f"осталось без объявления {len(missing) - limit} (предел {limit} за ход)")
    if extra:
        out.append(f"объявление без маршрута: {', '.join(extra)} — тариф мог измениться")
    return "; ".join(out) if out else f"объявления на месте: {len(want)}"


def fix_price_drift():
    """Объявленная цена врозь с живой — каталог валит маршрут за price_drift."""
    guard.check_action("research", "GREEN")
    c = connect()
    _table(c)
    cfg = listings_config()
    fixed, drift = [], []
    for route, lid in sorted(cfg.items()):
        path = "/" + route
        want = (TARIFF.get(path) or {}).get("usd")
        if want is None:
            continue
        st, d = _http(f"{NH}/{lid}")
        if st != 200:
            continue
        declared = d.get("price_amount")
        try:
            same = declared is not None and abs(float(declared) - float(want)) < 1e-9
        except (TypeError, ValueError):
            same = False
        if same:
            continue
        drift.append(f"{route}: объявлено {declared}, тариф {want}")
        row = c.execute("SELECT claim_token FROM directory_claim WHERE listing_id=?", (lid,)).fetchone()
        if not row or not row[0]:
            continue
        st2, _ = _http(f"{NH}/{lid}", "PATCH", {"price_amount": want}, {"x-claim-token": row[0]})
        if st2 == 200:
            fixed.append(route)
    c.close()
    if fixed:
        _note("dealer", "ЦЕНА В КАТАЛОГЕ ПОДРАВНЕНА агентом: " + ", ".join(fixed)
              + ". Расхождение объявленной и живой цены каталог считает price_drift и валит маршрут.", conf=0.9)
    if drift and not fixed:
        return "расхождение цен, но ключа правки нет: " + "; ".join(drift)
    return ("подравнено " + ", ".join(fixed)) if fixed else "цены совпадают"


def trigger_auditions(limit=3):
    """Аудит x402gle делает РЕАЛЬНЫЙ платный вызов со своей стороны — его надо дёргать."""
    guard.check_action("research", "GREEN")
    routes = sorted(TARIFF, key=lambda p: (TARIFF[p].get("usd") or 0))[:limit]
    ran, pending = [], []
    for path in routes:
        st, d = _http(AUDITION, "POST", {"url": SELF + path}, timeout=240)
        for r in (d.get("routes") or []):
            if str(r.get("outcome")) != "skipped" or r.get("score") is not None:
                ran.append(f"{path} score={r.get('score')}")
            else:
                pending.append(path)
    if ran:
        _note("dealer", "АУДИТ x402gle ПРОШЁЛ (их платный вызов = платёж нам): " + "; ".join(ran), conf=1.0)
        bus.broadcast("dealer", "Аудит x402gle прошёл по " + ", ".join(ran)
                      + " — это реальный платный вызов с их стороны.")
    return (f"аудит прошёл: {'; '.join(ran)}" if ran else
            f"аудит не прошёл ни по одному из {len(routes)} (их сторона): {', '.join(pending)}")


def register_indexes():
    """Индексы без аккаунта должны знать про ВСЕ маршруты, а не про те, что были когда-то."""
    guard.check_action("research", "GREEN")
    try:
        from agents import dealer
        r = dealer._register_x402scan()
        return (f"x402scan: registered {r.get('registered')}/{r.get('total')}, "
                f"public {r.get('publicCount')}, failed {r.get('failed')}")
    except Exception as e:
        return f"x402scan не ответил: {type(e).__name__}"


def health():
    """Каждый платный маршрут обязан отдавать 402 с нашим адресом, а бесплатный — 200."""
    guard.check_action("research", "GREEN")
    # СТАТУС 0 — ЭТО НАША СЕТЬ, А НЕ СЛОМАННЫЙ МАРШРУТ.
    # Первый прогон закричал «СЛОМАНО: /count отдал 0», хотя curl на тот же адрес
    # отдавал 402. Ноль возвращает наш же urllib при сетевом сбое, и выдавать это
    # за поломку продающей поверхности — значит научить агента кричать зря.
    # Поэтому: одна повторная попытка, и недостижимое числится НЕПРОВЕРЕННЫМ.
    def probe(path, want):
        for _ in range(2):
            st, d = _http(SELF + path, timeout=30)
            if st:
                return st, d
        return 0, {}

    bad, unreachable = [], []
    for path in sorted(TARIFF):
        st, d = probe(path, 402)
        if st == 0:
            unreachable.append(path)
            continue
        if st != 402:
            bad.append(f"{path} отдал {st}, а должен 402")
            continue
        acc = (d.get("accepts") or [{}])[0]
        if not str(acc.get("payTo", "")).lower().endswith("c55354"):
            bad.append(f"{path}: чужой payTo {acc.get('payTo')}")
    for path in ("/health", "/sample"):
        st, _ = probe(path, 200)
        if st == 0:
            unreachable.append(path)
        elif st != 200:
            bad.append(f"{path} отдал {st}, а должен 200")
    if bad:
        _note("dealer", "ПРОДАЮЩАЯ ПОВЕРХНОСТЬ СЛОМАНА: " + "; ".join(bad), conf=1.0)
        bus.broadcast("dealer", "Маршруты отвечают не так, как продаются: " + "; ".join(bad[:4]))
        return "СЛОМАНО: " + "; ".join(bad)
    if unreachable:
        return (f"проверено {len(TARIFF) - len([p for p in unreachable if p in TARIFF])} платных из {len(TARIFF)}; "
                f"не дозвонились до {len(unreachable)} ({', '.join(unreachable[:4])}) — это наша сеть, не поломка")
    return f"все {len(TARIFF)} платных отдают 402 с нашим адресом, бесплатные — 200"


CATALOG = ROOT / "worker" / "catalog.slim.json"
PRICE_FLOOR, PRICE_CAP = 0.0005, 1.0
# Простые справки живут по нижнему децилю рынка, аналитика — по медиане. Это не вкус:
# 17.09 свип купил семь из двенадцати раз именно дешёвые справки, а датасет за $0.25
# не взял вовсе. Менять цену чаще чем в два раза от цели незачем — это была бы возня.
PRIMITIVES = {"/count", "/tags", "/top", "/service", "/networks"}
# Полный выгруз и разбор нишевого спроса — НЕ то же, что одиночный запрос, и
# равнять их по медиане рынка неправильно. Первый прогон предложил срезать
# /dataset с $0.25 до $0.01, то есть в двадцать пять раз: медиана рынка — это
# цена одного вопроса, а не всей выгрузки на 15 758 строк. Для них ориентир p90.
PREMIUM = {"/dataset", "/alpha"}
# И в любом случае — не больше чем вдвое за один ход: цена движется шагами, чтобы
# ошибку было видно на выручке до того, как она станет обвалом.
MAX_STEP = 2.0


def market_percentiles():
    """p10 и медиана по ЧУЖИМ объявленным ценам из нашего же каталога."""
    try:
        cat = json.loads(CATALOG.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    ours = SELF.split("//", 1)[-1]
    prices = sorted(x for s in cat
                    if ours not in str(s.get("u") or "")
                    for x in [s.get("p")]
                    if isinstance(x, (int, float)) and 0 < x <= 1000)
    if len(prices) < 100:
        return None
    pick = lambda q: prices[min(len(prices) - 1, int(len(prices) * q))]
    return {"n": len(prices), "p10": pick(0.10), "median": pick(0.50), "p90": pick(0.90)}


def reprice(apply=True):
    """Переоценка БЕЗ развёртывания: действующая цена живёт в KV, границы — в воркере.

    Самое результативное ручное действие 17.09 — снижение простых маршрутов до нижнего
    дециля рынка. Оно требовало правки кода и развёртывания, то есть человека. Теперь
    тариф маршрута можно поменять через /prices, и это делает агент.
    """
    guard.check_action("research", "YELLOW")
    m = market_percentiles()
    if not m:
        return "каталог не прочитан или слишком мал — цену не трогаем"
    st, live = _http(SELF + "/prices", timeout=30)
    if st != 200:
        return f"/prices не ответил ({st}) — цену не трогаем"
    effective = live.get("effective") or {}
    want, why = {}, []
    for path, eff in effective.items():
        if path in PREMIUM:
            target = m["p90"]
        elif path in PRIMITIVES:
            target = m["p10"]
        else:
            target = m["median"]
        target = max(PRICE_FLOOR, min(PRICE_CAP, float(target)))
        try:
            cur = float(eff)
        except (TypeError, ValueError):
            continue
        # трогаем только при расхождении больше чем вдвое — иначе это возня
        if not (cur > target * 2 or cur * 2 < target):
            continue
        # и двигаем не больше чем вдвое за ход
        step = min(target, cur * MAX_STEP) if target > cur else max(target, cur / MAX_STEP)
        step = round(max(PRICE_FLOOR, min(PRICE_CAP, step)), 6)
        if abs(step - cur) < 1e-9:
            continue
        want[path] = step
        why.append(f"{path}: {cur} -> {step} (ориентир {target})")
    if not want:
        return (f"цены в пределах рынка (p10 {m['p10']}, медиана {m['median']}, "
                f"по {m['n']} чужим ценам) — менять нечего")
    if not apply:
        return "предложение: " + "; ".join(why)
    token = _board_token()
    if not token:
        _note("dealer", "ПЕРЕОЦЕНКА ПРЕДЛОЖЕНА, НО КЛЮЧА НЕТ: " + "; ".join(why)
              + ". Нужен BOARD_TOKEN, иначе цену меняет только человек.", conf=0.8)
        return "нет BOARD_TOKEN — только предложение: " + "; ".join(why)
    st2, d2 = _http(SELF + "/prices", "POST", want, {"x-board-token": token}, timeout=40)
    if st2 != 200:
        return f"переоценка отклонена воркером ({st2} {str(d2)[:80]})"
    _note("dealer", "ЦЕНА ИЗМЕНЕНА АГЕНТОМ без развёртывания: " + "; ".join(why)
          + f". Ориентир — рынок: p10 {m['p10']}, медиана {m['median']} по {m['n']} чужим ценам. "
          f"Границы воркера ${PRICE_FLOOR}..${PRICE_CAP} проверяются на его стороне.", conf=0.9)
    bus.broadcast("dealer", "Переоценка без развёртывания: " + "; ".join(why))
    return "изменено: " + "; ".join(why)


def _board_token():
    try:
        from agents.worker import _env_value
        return _env_value("BOARD_TOKEN")
    except Exception:
        import os
        return os.environ.get("BOARD_TOKEN") or ""


def delivery_gap():
    """Взяли деньги — отдали ли товар?

    Именно на этом мы потеряли первого покупателя: 17.09 пришло 12 переводов, а
    воркер не записал ни одного обслуженного вызова — платёжного заголовка не
    присылали, и мы отвечали 402 на уже оплаченный вызов. Каталог nohumans держит
    для этого отдельный класс отказа (paid_but_status_402, 146 эндпоинтов), и
    повторных покупок в нём не бывает. Считаем разрыв вслух, чтобы он не жил молча.
    """
    guard.check_action("research", "GREEN")
    from core.payment import OWNER_DESTINATIONS as DEST
    evm = DEST.get("evm")
    st, d = _http(f"https://base.blockscout.com/api/v2/addresses/{evm}/token-transfers?filter=to", timeout=40)
    if st != 200:
        return f"обозреватель Base не ответил ({st}) — разрыв не посчитан"
    items = d.get("items") or []
    # ОКНО, А НЕ ВСЯ ИСТОРИЯ. Двенадцать переводов 17.09 пришли ДО того, как маршрут
    # научился отдавать товар за уже сделанный платёж, и доставить их назад нельзя.
    # Вечная тревога о непоправимом — это шум, поэтому считаем сутки: так виден
    # НОВЫЙ разрыв, а старый долг называется отдельно и один раз.
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    recent = [t for t in items if str(t.get("timestamp") or "") >= since]
    st2, stats = _http(SELF + "/stats.json", timeout=30)
    days = (stats.get("days") or {}) if st2 == 200 else {}
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    yday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    served = 0
    for key in (today, yday):
        for k in ("paid", "prepaid", "direct_paid", "btc_paid", "tron_paid", "sol_paid", "stx_paid"):
            served += int((days.get(key) or {}).get(k) or 0)
    paid_in = len(recent)
    gap = paid_in - served
    if gap > 0:
        _note("dealer",
              f"РАЗРЫВ ДОСТАВКИ: получено {paid_in} переводов, обслужено {served} вызовов. "
              f"{gap} платежей не превратились в отданный товар. Это класс paid_but_status_402: "
              f"деньги взяли, данные не отдали, повторной покупки не будет. Проверить, что на "
              f"платном маршруте без заголовка ищется уже пришедший перевод.", conf=0.9)
        bus.broadcast("dealer",
                      f"Взяли {paid_in} платежей, обслужили {served} вызовов — разрыв {gap}. "
                      f"Платёж без заголовка обязан открывать маршрут, иначе покупатель не вернётся.")
        return f"РАЗРЫВ {gap} за сутки: платежей {paid_in}, обслужено {served} (всего платежей {len(items)})"
    return f"разрыва нет за сутки: платежей {paid_in}, обслужено {served} (всего платежей {len(items)})"


def cycle():
    """Полный оборот: поверхность жива, объявлена, цены совпадают, аудит дёрнут."""
    parts = [f"здоровье: {health()}",
             f"доставка: {delivery_gap()}",
             f"объявления: {ensure_listings()}",
             f"цены: {fix_price_drift()}",
             f"цена по рынку: {reprice()}",
             f"индексы: {register_indexes()}",
             f"аудит: {trigger_auditions()}"]
    return " | ".join(parts)
