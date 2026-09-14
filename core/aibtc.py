"""ДОСКА AIBTC (aibtc.com): подпись и сдача работы от имени кошелька агента.

Ключи живут ТОЛЬКО в Brain/.env (AIBTC_MNEMONIC) и никогда не попадают в
промпт модели: агент вызывает этот модуль, а подписывает Node-скрипт
ops/aibtc/aibtc_wallet.mjs (алгоритмы — как в официальном @aibtc/mcp-server).

Регистрацию (POST /api/register) выполняет ВЛАДЕЛЕЦ командой
`node ops/aibtc/aibtc_wallet.mjs register`: создание идентичности на площадке —
его действие. После неё агенты сдают работу сами: submit() подписывает строку
«AIBTC Bounty Submit | …» BIP-322 и шлёт её на /api/bounties/{id}/submit.
Одна сдача на агента на задачу (повтор — 409); правки — по тому же contentUrl.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.db import connect, ensure_schema  # noqa: E402

WALLET = ROOT / "ops" / "aibtc" / "aibtc_wallet.mjs"
SCHEMA = """
CREATE TABLE IF NOT EXISTS aibtc_submissions (
  id INTEGER PRIMARY KEY,
  bounty_id TEXT NOT NULL,
  btc_address TEXT,
  message TEXT NOT NULL,
  content_url TEXT,
  http INTEGER,
  response TEXT,
  submitted_at TEXT NOT NULL,
  UNIQUE(bounty_id, btc_address)
);
"""


class AibtcUnavailable(RuntimeError):
    """Кошелёк агента не создан или не зарегистрирован — сдавать нечем."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _has_mnemonic() -> bool:
    env = ROOT / ".env"
    if not env.exists():
        return False
    return any(line.startswith("AIBTC_MNEMONIC=") and len(line) > 30
               for line in env.read_text(encoding="utf-8-sig", errors="ignore").splitlines())


def _run(*args: str, timeout: int = 90) -> dict:
    if not WALLET.exists():
        raise AibtcUnavailable("нет ops/aibtc/aibtc_wallet.mjs")
    if not _has_mnemonic():
        raise AibtcUnavailable("кошелёк агента не создан: владелец запускает "
                               "`node ops/aibtc/aibtc_wallet.mjs create` и затем `register`")
    r = subprocess.run(["node", str(WALLET), *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout, cwd=str(ROOT))
    out = (r.stdout or "").strip()
    try:
        data = json.loads(out) if out.startswith("{") else {"raw": out}
    except ValueError:
        data = {"raw": out[:600]}
    data["exit"] = r.returncode
    if r.returncode not in (0, 2) and not out:
        data["stderr"] = (r.stderr or "")[:400]
    return data


def available() -> bool:
    """Есть ли у агента чем подписывать (seed создан владельцем)."""
    return WALLET.exists() and _has_mnemonic()


def addresses() -> dict:
    return _run("addresses")


def registered(transport=None) -> bool | None:
    """Зарегистрирован ли наш BTC-адрес на AIBTC (GET /api/agents/{address}); None — не узнали."""
    import urllib.request, urllib.error
    try:
        addr = addresses().get("btcAddress")
    except AibtcUnavailable:
        return False
    if not addr:
        return None
    try:
        with urllib.request.urlopen(urllib.request.Request(
                f"https://aibtc.com/api/agents/{addr}",
                headers={"User-Agent": "P0-agents/1.0", "Accept": "application/json"}), timeout=20) as r:
            return r.status == 200
    except urllib.error.HTTPError as e:
        return False if e.code == 404 else None
    except Exception:
        return None


def submit(bounty_id: str, message: str, content_url: str = "") -> dict:
    """Сдать работу: подпись BIP-322 и POST /api/bounties/{id}/submit. Один раз на задачу."""
    bounty_id = str(bounty_id).strip()
    message = " ".join(str(message).split())
    if not bounty_id or len(message) < 20:
        raise ValueError("нужны id задачи и содержательное сообщение (>= 20 символов)")
    c = connect()
    ensure_schema(c, SCHEMA)
    dup = c.execute("SELECT id, http FROM aibtc_submissions WHERE bounty_id=? AND http BETWEEN 200 AND 299",
                    (bounty_id,)).fetchone()
    c.close()
    if dup:
        return {"ok": False, "why": f"уже сдано (запись #{dup[0]}); правки — по прежнему contentUrl"}
    res = _run("submit", bounty_id, message, content_url or "", timeout=120)
    http = int(res.get("http") or 0)
    c = connect()
    ensure_schema(c, SCHEMA)
    c.execute("INSERT OR REPLACE INTO aibtc_submissions(bounty_id,btc_address,message,content_url,http,"
              "response,submitted_at) VALUES (?,?,?,?,?,?,?)",
              (bounty_id, res.get("btcAddress"), message, content_url or None, http,
               json.dumps(res.get("response"), ensure_ascii=False)[:2000], now()))
    c.commit(); c.close()
    return {"ok": 200 <= http < 300, "http": http, "bounty_id": bounty_id,
            "btcAddress": res.get("btcAddress"), "response": res.get("response")}


if __name__ == "__main__":
    print("кошелёк агента:", "есть" if available() else "не создан")
    if available():
        print(json.dumps(addresses(), ensure_ascii=False))
        print("зарегистрирован на AIBTC:", registered())
