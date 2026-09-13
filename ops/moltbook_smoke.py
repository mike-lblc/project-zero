"""Live read-only Moltbook smoke test; never prints credentials."""
from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def load_local_key() -> None:
    if os.environ.get("MOLTBOOK_API_KEY"):
        return
    path = ROOT / ".env"
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if line.startswith("MOLTBOOK_API_KEY="):
            os.environ["MOLTBOOK_API_KEY"] = line.partition("=")[2].strip().strip('"').strip("'")
            return
    raise RuntimeError("MOLTBOOK_API_KEY missing")


def main() -> int:
    load_local_key()
    from core import moltbook

    if "--heartbeat" in sys.argv:
        from agents import moltbook as community
        result = community.heartbeat(force=True)
        print(result)
        return 0 if result.get("ok") else 1

    names = moltbook.grant_registry_access()
    identity = moltbook.status()
    passed, failed = [], {}
    for name in names:
        try:
            moltbook.Channel(name).posts(limit=1)
            passed.append(name)
        except Exception as error:
            failed[name] = type(error).__name__
    print({"identity": identity, "agents": len(names),
           "live_read_passed": passed, "failed": failed})
    return 0 if identity["ok"] and len(passed) == len(names) and not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
