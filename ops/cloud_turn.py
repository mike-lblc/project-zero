"""One durable, bounded cloud reasoning turn. No owner-machine dependency.

Existing tool guards remain enforced. Does not claim other local-only steps
have migrated. The restored cloud database holds selection and usage history.
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main():
    if os.environ.get("P0_MODEL_BACKEND") != "cloudflare":
        raise RuntimeError("Cloud turn requires cloud backend; local fallback forbidden")
    from core import db, roster, agent
    from core import recall
    db.init()
    # Until cloud embedding migration, use the agent's SQL task/history memory.
    # Do not call localhost or mix embeddings from different model spaces.
    recall.recall = lambda *args, **kwargs: []
    con = db.connect()
    con.execute("CREATE TABLE IF NOT EXISTS cloud_turns (id INTEGER PRIMARY KEY, agent TEXT NOT NULL, started_at TEXT NOT NULL, outcome TEXT)")
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    used = con.execute("SELECT COUNT(*) FROM cloud_turns WHERE started_at LIKE ?", (day+'%',)).fetchone()[0]
    if used >= 96:
        print("Cloud daily request cap reached; wait for next UTC day")
        con.close()
        return 0
    names = sorted(roster.wire())
    last = con.execute("SELECT agent FROM cloud_turns ORDER BY id DESC LIMIT 1").fetchone()
    name = names[(names.index(last[0])+1)%len(names)] if last and last[0] in names else names[0]
    started = datetime.now(timezone.utc).isoformat()
    cur = con.execute("INSERT INTO cloud_turns(agent,started_at) VALUES (?,?)",(name,started))
    turn_id = cur.lastrowid
    con.commit()
    try:
        result = agent.get(name).act()
    except Exception as error:
        result = {"agent":name,"ok":False,"detail":type(error).__name__}
    con.execute("UPDATE cloud_turns SET outcome=? WHERE id=?",(json.dumps(result,ensure_ascii=False),turn_id))
    con.commit()
    con.close()
    print(json.dumps(result,ensure_ascii=False))
    return 0 if result.get("ok") else 1

if __name__ == '__main__':
    sys.exit(main())
