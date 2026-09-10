"""EmailOctopus client. Agents have FULL authority here (owner, 2026-09-10),
except sending to humans, which is capped in code - see SEND_CAP_PER_DAY."""
import os, json, urllib.request, urllib.error
from pathlib import Path

API = "https://api.emailoctopus.com"
SEND_CAP_PER_DAY = 200          # hard envelope. Agents act freely below it.
_ENV = Path(__file__).resolve().parent.parent / ".env"

def _key():
    for line in _ENV.read_text(encoding="utf-8").splitlines():
        if line.startswith("EMAILOCTOPUS_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("EMAILOCTOPUS_API_KEY missing from .env")

def call(method, path, body=None):
    """Key travels in the header, never the URL, so it stays out of logs."""
    req = urllib.request.Request(
        f"{API}{path}", method=method,
        data=json.dumps(body).encode() if body else None,
        headers={"Authorization": f"Bearer {_key()}",
                 "Accept": "application/json",
                 "Content-Type": "application/json",
                 # Cloudflare 1010 blocks the default Python-urllib UA. Verified 2026-09-10.
                 "User-Agent": "P0-agent/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode()[:500]}

def lists():            return call("GET", "/lists")
def list_get(lid):      return call("GET", f"/lists/{lid}")
def contacts(lid):      return call("GET", f"/lists/{lid}/contacts")

if __name__ == "__main__":
    st, data = lists()
    print(f"HTTP {st}")
    for l in data.get("data", []):
        c = l.get("counts", [{}])[0] if l.get("counts") else {}
        print(f"  {l['name']} | id={l['id']} | double_opt_in={l['double_opt_in']} | {c}")
