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
from datetime import datetime, timezone
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
    bad = []
    for path in sorted(TARIFF):
        st, d = _http(SELF + path, timeout=30)
        if st != 402:
            bad.append(f"{path} отдал {st}, а должен 402")
            continue
        acc = (d.get("accepts") or [{}])[0]
        if not str(acc.get("payTo", "")).lower().endswith("c55354"):
            bad.append(f"{path}: чужой payTo {acc.get('payTo')}")
    for path in ("/health", "/sample"):
        st, _ = _http(SELF + path, timeout=30)
        if st != 200:
            bad.append(f"{path} отдал {st}, а должен 200")
    if bad:
        _note("dealer", "ПРОДАЮЩАЯ ПОВЕРХНОСТЬ СЛОМАНА: " + "; ".join(bad), conf=1.0)
        bus.broadcast("dealer", "Маршруты отвечают не так, как продаются: " + "; ".join(bad[:4]))
        return "СЛОМАНО: " + "; ".join(bad)
    return f"все {len(TARIFF)} платных отдают 402 с нашим адресом, бесплатные — 200"


def cycle():
    """Полный оборот: поверхность жива, объявлена, цены совпадают, аудит дёрнут."""
    parts = [f"здоровье: {health()}",
             f"объявления: {ensure_listings()}",
             f"цены: {fix_price_drift()}",
             f"индексы: {register_indexes()}",
             f"аудит: {trigger_auditions()}"]
    return " | ".join(parts)
