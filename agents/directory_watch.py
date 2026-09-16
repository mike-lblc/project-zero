"""НАБЛЮДЕНИЕ ЗА КАТАЛОГОМ, КОТОРЫЙ ПЛАТИТ САМ (16.09.2026).

nohumans.directory — единственная найденная площадка, где покупатель платит НАМ, ничего
у нас не спрашивая. Их скаут `0x54E163e9B8eDDa194D83F46AdD921bfA5fc5f4E0` покупает у
объявленных эндпоинтов реальный USDC на Base волнами: 48 покупок 15.09, 2 — 16.09. Это
не заявка и не переговоры, а покупка, и потому первый платёж системы, скорее всего,
придёт отсюда.

Почему за этим нужно СЛЕДИТЬ, а не просто объявиться:
- Платная проверка — событие точечное. Если она прошла и сорвалась, каталог запомнит
  исход («заплатили и получили ошибку»), а мы об этом не узнаем никогда.
- Объявление живёт, только пока эндпоинт отвечает: подряд неудачные проверки снимают
  листинг с витрины молча.
- Хэш расчёта лежит в paid_verification.tx_hash — это и есть ончейн-доказательство
  первого стороннего платежа, ради которого всё делается. Его нужно записать в
  доказательства, а не потерять.

Ключи правки объявлений лежат в .env и сюда не попадают: наблюдение ничего не меняет,
ему хватает публичного чтения.
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

API = "https://api.nohumans.directory/v1/listings/"
SCOUT = "0x54E163e9B8eDDa194D83F46AdD921bfA5fc5f4E0"
UA = "P0-agent/1.0"


def now():
    return datetime.now(timezone.utc).isoformat()


def _note(agent, claim, conf=None):
    try:
        from agents.worker import note as _wnote
        _wnote(agent, claim, conf=conf)
    except Exception:
        pass


CONFIG = ROOT / "data" / "nohumans_listings.json"


def listing_ids():
    """Пары (маршрут, id объявления).

    Идентификаторы объявлений НЕ СЕКРЕТ — их читает кто угодно, — и лежат в репозитории,
    иначе облачный цикл не увидел бы платёж: .env остаётся только на этой машине, а
    именно облако ходит чаще. .env имеет приоритет: там правка руками.

    Ключи ПРАВКИ (claim_token) не читаются здесь ни при каких условиях: наблюдение
    ничего не меняет, и ключ, который не нужен, не должен быть под рукой.
    """
    found = {}
    if CONFIG.exists():
        try:
            found.update(json.loads(CONFIG.read_text(encoding="utf-8")).get("listings") or {})
        except (ValueError, OSError):
            pass
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip()
            if k.startswith("NOHUMANS_LISTING_") and v:
                found[k[len("NOHUMANS_LISTING_"):].lower()] = v
    return sorted(found.items())


def _get(lid, timeout=30):
    r = urllib.request.Request(API + lid, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return json.loads(resp.read().decode() or "{}")
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, OSError):
        return None


def _table(c):
    c.execute("CREATE TABLE IF NOT EXISTS directory_listing (id TEXT PRIMARY KEY, route TEXT, "
              "status TEXT, paid_verified INTEGER DEFAULT 0, paid_tx TEXT, failures INTEGER DEFAULT 0, "
              "probes INTEGER DEFAULT 0, checked_at TEXT)")


def check():
    """Состояние наших объявлений; платёж скаута записывается в доказательства один раз."""
    guard.check_action("research", "GREEN")
    ids = listing_ids()
    if not ids:
        return "объявлений nohumans в .env нет — наблюдать нечего"
    c = connect()
    _table(c)
    lines, paid_now, dropped, unreachable = [], [], [], 0
    for route, lid in ids:
        d = _get(lid)
        if not d:
            unreachable += 1
            continue
        status = str(d.get("status") or "")
        pv = d.get("paid_verification") or {}
        verified = bool(pv.get("verified"))
        tx = pv.get("tx_hash") or (d.get("paid_verification_latest") or {}).get("tx_hash")
        fails = int(d.get("consecutive_failures") or 0)
        probes = int(d.get("probe_count") or 0)
        prev = c.execute("SELECT status, paid_verified FROM directory_listing WHERE id=?", (lid,)).fetchone()
        # ПЛАТЁЖ — записываем один раз, при переходе, и вместе с хэшем: это ончейн-доказательство.
        if verified and not (prev and prev[1]):
            paid_now.append((route, tx))
            _note("dealer",
                  f"ПЕРВЫЙ СТОРОННИЙ ПЛАТЁЖ (nohumans.directory): скаут {SCOUT} купил у нашего "
                  f"маршрута /{route} за реальный USDC на Base и принял ответ. tx {tx}. "
                  f"Объявление {lid}.", conf=1.0)
        if prev and prev[0] == "verified" and status != "verified":
            dropped.append((route, status, fails))
        c.execute("INSERT INTO directory_listing(id,route,status,paid_verified,paid_tx,failures,probes,checked_at) "
                  "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET route=excluded.route, "
                  "status=excluded.status, paid_verified=excluded.paid_verified, "
                  "paid_tx=COALESCE(excluded.paid_tx, directory_listing.paid_tx), "
                  "failures=excluded.failures, probes=excluded.probes, checked_at=excluded.checked_at",
                  (lid, route, status, 1 if verified else 0, tx, fails, probes, now()))
        lines.append(f"/{route} {status}" + (" ОПЛАЧЕН" if verified else "") + (f" сбоев {fails}" if fails else ""))
    c.commit()
    c.close()
    if paid_now:
        bus.broadcast("dealer",
                      "ПЛАТЁЖ ПОЛУЧЕН. Каталог nohumans.directory купил у нас "
                      + ", ".join(f"/{r} (tx {t})" for r, t in paid_now)
                      + ". Это сторонний платёж реальным USDC на Base на кошелёк владельца. "
                      "Он же открывает нам ленты discovery: туда пускают только после проведённого "
                      "через фасилитатор платежа.")
    if dropped:
        bus.broadcast("dealer",
                      "Объявление сорвалось с проверки: "
                      + "; ".join(f"/{r} → {s}, подряд сбоев {f}" for r, s, f in dropped)
                      + ". Пока маршрут не отвечает, платить нам не будут.")
        for r, s, f in dropped:
            _note("dealer", f"nohumans: маршрут /{r} выпал из verified в {s} после {f} сбоев подряд", conf=0.9)
    out = "; ".join(lines) if lines else "ни одно объявление не прочиталось"
    if unreachable:
        out += f"; недоступно {unreachable}"
    return out
