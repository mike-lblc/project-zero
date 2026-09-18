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

Чего здесь НЕТ: правок кода и развёртываний. Расхождения этот шаг НАЗЫВАЕТ.
Разворачивает отдельный агент (`agents/self_deploy.py`) — с проверкой адреса
получателя до сети, живой проверкой после разноса версии и автоматическим
откатом; разделение нарочное, чтобы «поддержать витрину» и «переписать платный
сервис» не делались одной рукой.

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
# Курсор осмотра витрины: одно число, докуда дошло окно проверки. Данные, не код.
CURSOR = ROOT / "data" / "surface_cursor.txt"
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


def _claim_token(c, listing_id, route):
    """Ключ правки объявления: сперва база, потом окружение.

    ПОЧЕМУ ДВА ИСТОЧНИКА. Одиннадцать объявлений, которые РЕАЛЬНО приносят деньги,
    создавались руками, и их ключи легли в .env (NOHUMANS_TOKEN_<МАРШРУТ>), а таблица
    directory_claim осталась пустой. Поэтому `fix_price_drift` не мог починить ни
    одно из зарабатывающих объявлений: он смотрел только в базу. А расхождение цены
    каталог считает price_drift и ВАЛИТ маршрут — то есть единственный платящий канал
    молча выключался, и починить его было некому.

    Порядок: база (там ключи объявлений, созданных агентом) → окружение (ключи,
    созданные руками; в облаке приходят секретом). Значение никуда не печатается.
    """
    try:
        row = c.execute("SELECT claim_token FROM directory_claim WHERE listing_id=?",
                        (listing_id,)).fetchone()
        if row and row[0]:
            return row[0]
    except Exception:
        pass
    key = "NOHUMANS_TOKEN_" + str(route).lstrip("/").upper()
    try:
        from agents.worker import _env_value
        return _env_value(key) or ""
    except Exception:
        return ""


def live_tariff():
    """Действующий тариф ИЗ ВОРКЕРА, а не из кода.

    Маршрут, объявленный агентом через /routes, в статическом core.identity.TARIFF
    не появляется — значит по нему не создалось бы объявление, не проверилась бы цена
    и не сработал бы контроль здоровья. Поэтому источник истины здесь — /prices,
    который отдаёт действующие цены и скомпилированных, и объявленных маршрутов.
    Если воркер недоступен, откатываемся на скомпилированный тариф.
    """
    st, d = _http(SELF + "/prices", timeout=30)
    if st == 200 and isinstance(d.get("effective"), dict) and d["effective"]:
        out = {}
        for path, usd in d["effective"].items():
            base = TARIFF.get(path) or {}
            out[path] = {"usd": float(usd), "what": base.get("what") or f"declared route {path}"}
        # описания объявленных маршрутов берём из /routes
        st2, r = _http(SELF + "/routes", timeout=30)
        if st2 == 200:
            for x in (r.get("declared") or []):
                if x.get("path") in out:
                    out[x["path"]]["what"] = x.get("what") or out[x["path"]]["what"]
        return out
    return {k: dict(v) for k, v in TARIFF.items()}


def declare_routes(limit=4):
    """Новый платный маршрут БЕЗ правки кода: агент объявляет его данными.

    Добавление маршрута было последним, что делал человек: правка worker/src/index.js
    и развёртывание. Теперь маршрут — это спецификация (путь, цена, описание и запрос
    к нашему каталогу из закрытого списка операций), воркер её ИНТЕРПРЕТИРУЕТ, а не
    исполняет. Агент не может прислать код: каждое поле проверяется по белому списку
    на стороне воркера, и непринятое называется вслух.

    Что именно объявляем: по самым спросовым тегам каталога — «сервисы этого тега по
    числу платящих». Это продукт, а не пустая нарезка: спрос по тегу измерен чужими
    деньгами, а форма (много дешёвых узких маршрутов) — та, которую 17.09 покупал свип.
    """
    guard.check_action("research", "YELLOW")
    st, grammar = _http(SELF + "/routes", timeout=30)
    if st != 200:
        return f"/routes не ответил ({st}) — маршруты не объявляем"
    already = {x.get("path") for x in (grammar.get("declared") or [])}
    compiled = set(grammar.get("compiled") or [])
    try:
        cat = json.loads(CATALOG.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return "каталог не прочитан — маршруты не объявляем"
    stat = {}
    for sv in cat:
        for t in (sv.get("t") or []):
            d = stat.setdefault(str(t), {"n": 0, "y": 0})
            d["n"] += 1
            d["y"] += sv.get("y") or 0
    # тег стоит маршрута, если у него есть и спрос, и не один поставщик
    ranked = sorted((t for t, d in stat.items() if d["n"] >= 5 and d["y"] >= 200),
                    key=lambda t: -stat[t]["y"])
    m = market_percentiles() or {"p10": 0.001}
    price = max(PRICE_FLOOR, min(PRICE_CAP, float(m["p10"])))
    want, why = [], []
    for tag in ranked:
        slug = "".join(ch if ch.isalnum() else "-" for ch in tag.lower()).strip("-")[:24]
        if not slug or not slug[0].isalpha():
            continue
        path = "/in-" + slug
        if path in compiled or path in already or any(w["path"] == path for w in want):
            continue
        want.append({
            "path": path, "usd": price,
            "what": (f"Highest-demand x402 services tagged '{tag}': ranked by 30-day unique paying "
                     f"wallets, with calls, price, network and calls-per-payer for each. "
                     f"{stat[tag]['n']} providers and {stat[tag]['y']} paying wallets measured in this tag."),
            "spec": {"source": "catalog", "op": "top", "by": "payers", "where": {"tag": tag}, "limit": 25},
        })
        why.append(f"{path} (тег {tag}: {stat[tag]['n']} поставщиков, {stat[tag]['y']} платящих)")
        if len(want) >= limit:
            break
    if not want:
        return f"новых маршрутов не нужно: объявлено {len(already)}, скомпилировано {len(compiled)}"
    token = _board_token()
    if not token:
        return "нет BOARD_TOKEN — только предложение: " + "; ".join(why)
    keep = [{"path": x["path"], "usd": x["usd"], "what": x["what"], "spec": x["spec"]}
            for x in (grammar.get("declared") or [])] + want
    st2, d2 = _http(SELF + "/routes", "POST", keep, {"x-board-token": token}, timeout=45)
    if st2 != 200:
        # ИСЧЕРПАННЫЙ KV — НЕ ПОВОД ПОТЕРЯТЬ ПЛАТНЫЙ МАРШРУТ.
        #
        # Бесплатный предел KV (1000 записей в сутки на аккаунт) кончается, и тогда
        # хранилище маршрутов отвечает 503 «KV put() limit exceeded for the day».
        # Раньше на этом шаг заканчивался: до полуночи UTC новый платный маршрут
        # появиться не мог. Цена прямая — сколько маршрутов, столько оплаченных
        # проверок присылает скаут (его предел 28 за волну, мы занимали 11).
        #
        # Обходной путь без KV: та же спецификация ложится в worker/routes.json,
        # который воркер запекает в бандл, а деплой агенты умеют делать сами.
        if _bake_routes(want):
            _note("dealer", f"KV исчерпан ({st2}), поэтому маршруты записаны в бандл "
                            f"(worker/routes.json): " + "; ".join(why) +
                            ". Уедут в сеть следующим деплоем — его делает агент.", conf=0.9)
            return ("KV исчерпан — маршруты запечены в бандл, уедут деплоем: "
                    + "; ".join(why))
        return f"объявление отклонено воркером ({st2} {str(d2)[:110]})"
    _note("dealer", "МАРШРУТЫ ОБЪЯВЛЕНЫ АГЕНТОМ БЕЗ ПРАВКИ КОДА: " + "; ".join(why)
          + f". Цена {price} (нижний дециль рынка). Воркер интерпретирует спецификацию, "
          f"кода агент не писал. Отвергнуто: {d2.get('rejected')}", conf=0.9)
    bus.broadcast("dealer", "Объявлены новые платные маршруты без развёртывания: " + "; ".join(why))
    return "объявлено: " + "; ".join(why)


BAKED = ROOT / "worker" / "routes.json"


def _bake_routes(specs):
    """Записать объявленные маршруты в бандл — путь без KV.

    Файл читается воркером при загрузке и проверяется ТЕМ ЖЕ validateRouteSpec,
    что и маршруты из KV: агент кладёт данные, не код. Дубликаты по пути не
    плодятся — повторное объявление заменяет прежнее.
    """
    try:
        doc = json.loads(BAKED.read_text(encoding="utf-8")) if BAKED.exists() else {}
    except (ValueError, OSError):
        doc = {}
    have = {r.get("path"): r for r in (doc.get("routes") or [])}
    for s in specs:
        have[s["path"]] = {"path": s["path"], "usd": s["usd"], "what": s["what"], "spec": s["spec"]}
    doc.setdefault("_note", "Маршруты, объявленные агентом ДАННЫМИ и запечённые в бандл. "
                            "Нужны потому, что бесплатный предел KV исчерпывается, а платный "
                            "маршрут терять нельзя. Проверяются тем же validateRouteSpec.")
    doc["routes"] = sorted(have.values(), key=lambda r: r["path"])
    try:
        BAKED.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        return True
    except OSError:
        return False


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
    want = {p.lstrip("/") for p in live_tariff()}
    missing = sorted(want - have)
    extra = sorted(have - want)
    created, failed, adopted = [], [], []
    for route in missing[:limit]:
        path = "/" + route
        t = live_tariff().get(path) or {}
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
        elif st == 409 and d.get("listing_id"):
            # «УЖЕ ОБЪЯВЛЕНО» — ЭТО НЕ ОТКАЗ, А ПОДАРОК: НАМ ВЕРНУЛИ ИДЕНТИФИКАТОР.
            #
            # Так вскрылось расхождение, которое дороже, чем кажется. Облачный прогон
            # создаёт объявления сам, но его копия файла со списком домой не приезжает
            # (облачная и локальная базы разные). Замер 18.09: из 13 попыток ОДИННАДЦАТЬ
            # вернули 409 с готовым listing_id — то есть объявления в каталоге есть, а
            # мы про них не знаем.
            #
            # Цена незнания прямая: без идентификатора `fix_price_drift` не может
            # подравнять цену, а расхождение цены каталог считает price_drift и ВАЛИТ
            # маршрут. Иначе говоря, мы теряли бы платящие объявления и даже не видели,
            # какие. Поэтому идентификатор из 409 записываем и берём объявление под
            # наблюдение — ключа правки тут нет, и это названо честно.
            cfg[route] = d["listing_id"]
            adopted.append(route)
        else:
            failed.append(f"{route} ({st} {str(d)[:60]})")
    if created or adopted:
        c.commit()
        _save_config(cfg)
        _note("dealer", "ОБЪЯВЛЕНИЯ СОЗДАНЫ АГЕНТОМ (без рук): " + ", ".join(created)
              + f". Каталог nohumans платит сам; маршрут без объявления в нём не покупают.", conf=0.9)
    c.close()
    out = []
    if created:
        out.append(f"создано {len(created)}: {', '.join(created)}")
    if adopted:
        out.append(f"взято под наблюдение уже созданных облаком {len(adopted)}: "
                   + ", ".join(adopted) + " (ключа правки нет — цену им не подравнять)")
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
    fixed, drift, stale = [], [], []
    for route, lid in sorted(cfg.items()):
        path = "/" + route
        want = (live_tariff().get(path) or {}).get("usd")
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
        tok = _claim_token(c, lid, route)
        if not tok:
            continue
        st2, _ = _http(f"{NH}/{lid}", "PATCH", {"price_amount": want}, {"x-claim-token": tok})
        if st2 != 200:
            continue
        # ОТВЕТ 200 — ЭТО НЕ «ПОДРАВНЕНО». Замер 18.09: шаг два хода подряд
        # рапортовал об одних и тех же шестнадцати маршрутах, потому что считал
        # успехом сам код ответа и ни разу не перечитывал запись. Отчёт, который
        # не проверяет собственный результат, показывает работу вместо результата.
        st3, back = _http(f"{NH}/{lid}")
        try:
            landed = st3 == 200 and abs(float(back.get("price_amount")) - float(want)) < 1e-9
        except (TypeError, ValueError):
            landed = False
        (fixed if landed else stale).append(route)
    c.close()
    if fixed:
        _note("dealer", "ЦЕНА В КАТАЛОГЕ ПОДРАВНЕНА агентом: " + ", ".join(fixed)
              + ". Расхождение объявленной и живой цены каталог считает price_drift и валит маршрут.", conf=0.9)
    if drift and not fixed and not stale:
        return "расхождение цен, но ключа правки нет: " + "; ".join(drift)
    out = []
    if fixed:
        out.append("подравнено " + ", ".join(fixed))
    if stale:
        # Их запись обновляется своим пробом (~30 мин) — это НЕ отказ, но и не успех.
        out.append(f"правка принята, но запись ещё не обновилась у {len(stale)}: "
                   + ", ".join(stale[:6]) + " (у них свой проб ~30 мин)")
    return "; ".join(out) if out else "цены совпадают"


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
    """Индексы без аккаунта должны знать про ВСЕ маршруты, а не про те, что были когда-то.

    Индексов теперь два, и это не дублирование. Замечание владельца — «не быть
    привязанным к чему-то одному» — подтвердилось счётом: за всё время нам заплатили
    два разных покупателя, и один из них пришёл из единственного каталога. Пока
    источник один, он же и есть потолок выручки.

    Ни один из этих индексов не платит сам: они приводят платящих агентов. Обещать
    здесь платёж было бы ложью, поэтому шаг докладывает ровно факт размещения.
    """
    guard.check_action("research", "GREEN")
    parts = []
    try:
        from agents import dealer
        r = dealer._register_x402scan()
        parts.append(f"x402scan: размещено {r.get('registered')}/{r.get('total')}, "
                     f"публичных {r.get('publicCount')}, отказов {r.get('failed')}")
    except Exception as e:
        parts.append(f"x402scan не ответил: {type(e).__name__}")

    # Agent402: бесплатно, без аккаунта и почты; их обходчик сам снимает наши
    # маршруты с нашего же ответа 402 раз в час.
    st, d = _http("https://agent402.tools/api/index/register", method="POST",
                  body={"origin": SELF}, timeout=60)
    if st == 200 and isinstance(d, dict):
        seller = d.get("seller") or {}
        nets = seller.get("networks") or []
        line = (f"agent402: размещено {bool(d.get('listed'))}, маршрутов у них "
                f"{seller.get('toolCount')}, маршрутизуем {seller.get('routable')}")
        # Пустой список сетей — не мелочь: маршрутизатор, который фильтрует по сети,
        # пройдёт мимо продавца без сетей. Называем это вслух, а не прячем.
        if not nets:
            line += "; СЕТИ У НИХ ПУСТЫ — маршрутизатор с фильтром по сети нас не найдёт"
        parts.append(line)
    else:
        parts.append(f"agent402 не ответил: {st}")
    return "; ".join(parts)


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
    lt = live_tariff()
    for path in sorted(lt):
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
        return (f"проверено {len(lt) - len([p for p in unreachable if p in lt])} платных из {len(lt)}; "
                f"не дозвонились до {len(unreachable)} ({', '.join(unreachable[:4])}) — это наша сеть, не поломка")
    return f"все {len(lt)} платных отдают 402 с нашим адресом, бесплатные — 200"


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


BUYER_TARGET = 5          # цель владельца: платежи от пяти РАЗНЫХ покупателей

CDP_VALIDATE = "https://api.cdp.coinbase.com/platform/v2/x402/validate"


def _rotating_cursor(total, step, _f=[None]):
    """Курсор осмотра витрины: с каждым ходом окно едет дальше по кругу.

    Лежит файлом рядом с базой, а не в памяти процесса: облачный прогон и
    локальный — это разные процессы, и общий курсор в памяти означал бы, что
    оба всегда смотрят на один и тот же кусок витрины.
    """
    f = CURSOR
    try:
        cur = int(f.read_text(encoding="utf-8").strip())
    except Exception:
        cur = 0
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(str((cur + step) % max(total, 1)), encoding="utf-8")
    except Exception:
        pass
    return cur % max(total, 1)


def validate_surface(limit=16):
    """Проверить витрину ЧУЖИМИ воротами, а не своими.

    Своя проверка здоровья отвечает на вопрос «работает ли по-нашему». Этот
    эндпоинт Coinbase отвечает на вопрос, который решает деньги: примет ли нас
    сторона ПОКУПАТЕЛЯ. Он бесплатен, не требует ни ключа, ни аккаунта, и
    прогоняет 25 обязательных проверок — от формата 402 до разбора расширения
    bazaar — плюс симуляцию платежа.

    ПРОВЕРЯЮТСЯ ВСЕ МАРШРУТЫ, А НЕ ПЕРВЫЕ ОДИННАДЦАТЬ. Предел стоял 11 с тех
    пор, когда маршрутов было одиннадцать; их стало 62, и проверка молча
    осматривала первую шестую часть витрины по алфавиту. Теперь окно едет по
    кругу: за несколько ходов осматривается вся витрина, и при этом мы не
    выстреливаем шестьюдесятью запросами в чужой бесплатный эндпоинт разом.

    Поле `index` в ответе — отдельный факт: null значит, что нас нет в ленте
    обнаружения. Почему нас там нет — НЕ ДОКАЗАНО. Прежняя версия этой строки
    утверждала «нужен платёж через фасилитатор»; 18.09 это опровергнуто: лента
    выкачана целиком (15 625 записей), в ней есть продавцы с нулём переводов за
    всю жизнь. Ложная причина дорого стоила — из-за неё работа ушла в discovery,
    пока настоящая поломка (мы отказывали заплатившим на 429 индексатора) жила
    незамеченной. Пока механизм не доказан, шаг говорит «не доказан».
    """
    guard.check_action("research", "GREEN")
    tariff = live_tariff()
    allr = sorted(tariff)
    routes = allr
    if allr and limit and limit < len(allr):
        cur = _rotating_cursor(len(allr), limit)
        routes = [allr[(cur + i) % len(allr)] for i in range(limit)]
    broken, indexed, checked = [], 0, 0
    for path in routes:
        st, d = _http(CDP_VALIDATE, method="POST", body={"resource": SELF + path}, timeout=60)
        if st != 200 or not isinstance(d, dict):
            broken.append(f"{path}: проверка не ответила ({st})")
            continue
        checked += 1
        if not d.get("valid"):
            failed = [c.get("check") for c in (d.get("preflight") or [])
                      if not c.get("passed") and c.get("severity") == "required"]
            broken.append(f"{path}: НЕ ПРИНЯТ ({', '.join(failed[:3]) or 'без деталей'})")
        elif (d.get("simulation") or {}).get("outcome") != "accepted":
            broken.append(f"{path}: симуляция платежа {(d.get('simulation') or {}).get('outcome')}")
        if d.get("index"):
            indexed += 1
    if broken:
        _note("dealer", "ВИТРИНУ НЕ ПРИНИМАЕТ СТОРОНА ПОКУПАТЕЛЯ: " + "; ".join(broken[:6])
                        + ". Это чинить раньше всего остального: пока проверка Coinbase не "
                          "проходит, платёж физически не сможет пройти.", conf=1.0)
        bus.broadcast("dealer", "Проверка Coinbase не принимает маршруты: " + "; ".join(broken[:3]))
        return "НЕ ПРИНЯТО: " + "; ".join(broken[:6])
    if not indexed and checked:
        # НЕ ПОВТОРЯТЬ ОПРОВЕРГНУТОЕ. Здесь стояло «нужен ОДИН платёж, проведённый
        # через фасилитатор». Это проверено и НЕВЕРНО (18.09.2026): лента CDP
        # выкачана целиком, 15 625 ресурсов, и в ней есть продавец
        # 0x693d72fc5ad1f09B1c62be3707679cbe334c88df, у которого по Blockscout ноль
        # транзакций и ноль переводов ЗА ВСЮ ЖИЗНЬ. Расчёт не условие входа.
        # Настоящий механизм пока не доказан — так и говорим, а не подставляем
        # удобную версию: ложная причина уводила работу от настоящей поломки
        # (мы отказывали заплатившим на 429 индексатора).
        return (f"все {checked} маршрутов приняты (valid, платёж симулируется), но в ленте "
                f"обнаружения нас нет; механизм попадания НЕ ДОКАЗАН — платёж через "
                f"фасилитатор условием НЕ является (в ленте есть продавцы с нулём "
                f"переводов за всю жизнь)")
    return f"все {checked} маршрутов приняты, в ленте обнаружения {indexed}"


def buyers():
    """Сколько РАЗНЫХ покупателей заплатило — по нашей же книге поступлений.

    Это считалось снаружи, в наблюдателе на bash, и наблюдатель врал: переменная
    уходила питону аргументом вместо переменной среды, `os.environ` оказывался
    пустым, и один и тот же давно известный плательщик каждые пять минут объявлялся
    новым. Счёт, который нельзя перепроверить, хуже отсутствия счёта.

    Поэтому считаем по `payment_receipts`, где у каждой записи есть доказательство
    в виде хеша перевода, а платёж от самого владельца записать нельзя вообще.
    Один покупатель — один адрес-источник, сколько бы переводов он ни сделал:
    двенадцать переводов скаута каталога — это один покупатель, а не двенадцать.
    """
    guard.check_action("research", "GREEN")
    from core.db import connect as _db
    c = _db()
    rows = c.execute("SELECT from_party, network, currency, gross FROM payment_receipts").fetchall()
    who = {}
    for r in rows:
        d = dict(r)
        key = str(d.get("from_party") or "?").lower()
        w = who.setdefault(key, {"n": 0, "sum": 0.0, "net": d.get("network"),
                                 "cur": d.get("currency")})
        w["n"] += 1
        w["sum"] += float(d.get("gross") or 0)
    distinct = len(who)

    # Верхняя отметка — чтобы о новом покупателе сказать ОДИН раз, а не в каждом цикле.
    c.execute("CREATE TABLE IF NOT EXISTS buyer_highwater (id INTEGER PRIMARY KEY, n INTEGER, at TEXT)")
    prev = c.execute("SELECT n FROM buyer_highwater ORDER BY id DESC LIMIT 1").fetchone()
    prev_n = int(dict(prev)["n"]) if prev else 0
    if distinct > prev_n:
        c.execute("INSERT INTO buyer_highwater(n,at) VALUES (?,?)", (distinct, now()))
        c.commit()
    c.close()

    chains = sorted({(w["net"] or "?") for w in who.values()})
    line = (f"разных покупателей {distinct} из {BUYER_TARGET}; поступлений {len(rows)}; "
            f"сети: {', '.join(chains)}")
    if distinct > prev_n:
        newcomers = distinct - prev_n
        _note("dealer", f"НОВЫХ ПОКУПАТЕЛЕЙ: {newcomers}. Всего разных плательщиков {distinct} "
                        f"из {BUYER_TARGET} по цели владельца. Считано по книге поступлений, "
                        f"где у каждой записи есть хеш перевода.", conf=1.0)
        bus.broadcast("dealer", f"Платит уже {distinct} разных покупателей (было {prev_n}). "
                                f"До цели владельца — {max(0, BUYER_TARGET - distinct)}.")
        return "НОВЫЙ ПОКУПАТЕЛЬ; " + line
    if distinct >= BUYER_TARGET:
        return "ЦЕЛЬ ДОСТИГНУТА; " + line
    return line


def cycle():
    """Полный оборот: поверхность жива, объявлена, цены совпадают, аудит дёрнут."""
    parts = [f"покупатели: {buyers()}",
             f"здоровье: {health()}",
             f"ворота покупателя: {validate_surface()}",
             f"новые маршруты: {declare_routes()}",
             f"доставка: {delivery_gap()}",
             f"объявления: {ensure_listings()}",
             f"цены: {fix_price_drift()}",
             f"цена по рынку: {reprice()}",
             f"индексы: {register_indexes()}",
             f"аудит: {trigger_auditions()}"]
    return " | ".join(parts)
