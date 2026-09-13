"""Import the four real API observations from the verification session once."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import db, moltbook  # noqa: E402


ROWS = (
    ("channel_manager", "profile_edit", None, None,
     "6e239533-e424-4bdb-a812-add25a40f227", "CONFIRMED", 200, "claimed",
     "PATCH /agents/me was confirmed by an authenticated GET with exact description readback"),
    ("orchestrator", "post", None, None,
     "5c8fe318-0285-4cf2-b219-197842b69205", "PENDING_VERIFICATION", 201, "pending",
     "POST returned success; GET /posts/{id} found the row with verification_status=pending"),
    ("verifier", "comment", "5c8fe318-0285-4cf2-b219-197842b69205", None,
     "e10a7beb-fc77-4df8-933c-baa1bc297735", "PENDING_VERIFICATION", 201, "pending",
     "comment exists in own comment history but is absent from the public thread"),
    ("watchdog", "reply", "5c8fe318-0285-4cf2-b219-197842b69205",
     "e10a7beb-fc77-4df8-933c-baa1bc297735",
     "9375166f-f157-477d-af78-286d7825a551", "PENDING_VERIFICATION", 201, "pending",
     "reply request returned an id; own history omits parent fields while verification is pending"),
    ("orchestrator", "edit", "5c8fe318-0285-4cf2-b219-197842b69205", None,
     "5c8fe318-0285-4cf2-b219-197842b69205", "INCONSISTENT", 200, "pending",
     "PATCH returned 'Post updated!' but exact GET readback showed unchanged content and updated_at"),
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    args = parser.parse_args()
    db.DB_PATH = args.db.resolve()
    db._WAL_SET = False
    db._SCHEMA_DONE.clear()
    c = moltbook._con()
    inserted = 0
    for actor, action, target, parent, external, state, http, verification, detail in ROWS:
        digest = hashlib.sha256(json.dumps(
            ["observed-live-2026-09-13", actor, action, target, parent, external],
            separators=(",", ":"), sort_keys=True).encode()).hexdigest()
        before = c.total_changes
        c.execute(
            "INSERT OR IGNORE INTO moltbook_receipts(agent,action,target_id,parent_id,request_hash,"
            "state,external_id,http_status,verification_status,detail,created_at,checked_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (actor, action, target, parent, digest, state, external, http, verification,
             detail, moltbook.now(), moltbook.now()),
        )
        inserted += c.total_changes - before
    c.commit()
    total = c.execute("SELECT COUNT(*) FROM moltbook_receipts").fetchone()[0]
    states = {row[0]: row[1] for row in c.execute(
        "SELECT state,COUNT(*) FROM moltbook_receipts GROUP BY state")}
    c.close()
    agents = moltbook.grant_registry_access()
    print({"inserted": inserted, "total": total,
           "states": states,
           "agents_with_access": len(agents)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
