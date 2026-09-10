"""Blast radius controls. Limits live in CODE, not in prompts - prompts can be
argued with, code cannot. DECISION_PROTOCOL.md section 5."""
from pathlib import Path
from datetime import date
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.db import connect

KILL_SWITCH = Path(__file__).resolve().parent.parent / "data" / "KILL_SWITCH"
CAPS = {"email_send": 200, "email_tag": 200, "page_publish": 20, "public_post": 10}
CLASSES = {"GREEN","YELLOW","RED","BLACK"}

# Что мы ПРОДАЁМ. Если это оказалось в открытом доступе — мы раздаём собственный товар.
# Проверки на это не было, и полный датасет за $1.25 пролежал бесплатно, пока владелец
# не заметил сам. Ни один агент не поймал: никто и не смотрел.
PAID_PRODUCTS = {
    "полный датасет": {"paths": ["docs/x402-market.json", "docs/x402-market.csv"],
                       "price": 1.25, "endpoint": "/dataset"},
    "отчёт по рынку": {"paths": ["docs/report.json", "docs/market-report.json"],
                       "price": 0.10, "endpoint": "/report"},
    "анализ ниш":     {"paths": ["docs/alpha.json", "docs/opportunities.json"],
                       "price": 0.50, "endpoint": "/alpha"},
}
PUBLIC_DIRS = ["docs"]
MAX_FREE_SAMPLE_ROWS = 150          # больше — это уже не образец, а продукт


class GivingAwayProduct(Exception): pass


class Halted(Exception): pass
class CapExceeded(Exception): pass
class Forbidden(Exception): pass

def check_alive():
    if KILL_SWITCH.exists():
        raise Halted("KILL_SWITCH present - all agent action halted.")

def check_paid_product_not_public():
    """Не выложен ли платный товар бесплатно. Запускается в каждом аудите
    и перед любой публикацией."""
    import json
    root = Path(__file__).resolve().parent.parent
    violations = []
    for name, spec in PAID_PRODUCTS.items():
        for rel in spec["paths"]:
            f = root / rel
            if f.exists():
                violations.append(f"{name} (${spec['price']}, тариф {spec['endpoint']}) "
                                  f"лежит открыто: {rel}")
    # любой крупный набор данных в публичной папке подозрителен
    for d in PUBLIC_DIRS:
        pub = root / d
        if not pub.exists():
            continue
        for f in pub.rglob("*"):
            if f.suffix.lower() not in (".json", ".csv") or not f.is_file():
                continue
            if "sample" in f.name.lower() or "stats" in f.name.lower():
                continue
            try:
                if f.suffix.lower() == ".json":
                    data = json.loads(f.read_text(encoding="utf-8"))
                    n = len(data) if isinstance(data, list) else 0
                else:
                    n = sum(1 for _ in f.open(encoding="utf-8")) - 1
            except Exception:
                continue
            if n > MAX_FREE_SAMPLE_ROWS:
                violations.append(f"{f.relative_to(root)}: {n} строк в открытой папке "
                                  f"(предел образца {MAX_FREE_SAMPLE_ROWS})")
    return violations


def check_action(kind, action_class):
    check_alive()
    if action_class == "BLACK":
        raise Forbidden(f"'{kind}' is BLACK: agents may never do this (accounts, "
                        f"KYC, CAPTCHA, spending, moving funds).")
    if action_class not in CLASSES:
        raise Forbidden(f"unknown action class {action_class!r}")
    if kind in ("publish_dataset", "page_publish", "publish"):
        v = check_paid_product_not_public()
        if v:
            raise GivingAwayProduct("публикация отменена — раздаём платное: " + "; ".join(v))
    cap = CAPS.get(kind)
    if cap is not None:
        con = connect()
        n = con.execute(
            "SELECT COUNT(*) FROM actions WHERE kind=? AND dry_run=0 AND date(created_at)=?",
            (kind, date.today().isoformat())).fetchone()[0]
        if n >= cap:
            raise CapExceeded(f"'{kind}' hit today's cap ({cap}). Agents cannot exceed this.")
    return True
