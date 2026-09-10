"""THE COUNCIL — 5 agents per DECISION_PROTOCOL.md.

CRITICAL HONESTY: judgment roles (Proposer/Adversary/Judge) may NOT run on the
local model. They enqueue escalations resolved by a frontier model when a Claude
Code session runs. Only Verifier's fact-gathering and the Orchestrator's plumbing
run continuously and locally.
"""
import sys, json
from pathlib import Path
from datetime import datetime, timezone
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.db import connect
from core import guard

def now(): return datetime.now(timezone.utc).isoformat()

# ---------------------------------------------------------------- bus
def send(sender, body, recipient=None, topic=None):
    con = connect()
    con.execute("INSERT INTO messages(sender,recipient,topic,body,created_at) VALUES (?,?,?,?,?)",
                (sender, recipient, topic, body, now())); con.commit()

def inbox(agent, consume=True):
    con = connect()
    rows = con.execute("SELECT * FROM messages WHERE (recipient=? OR recipient IS NULL) "
                       "AND consumed_at IS NULL ORDER BY id", (agent,)).fetchall()
    if consume and rows:
        con.executemany("UPDATE messages SET consumed_at=? WHERE id=?",
                        [(now(), r["id"]) for r in rows]); con.commit()
    return [dict(r) for r in rows]

# ---------------------------------------------------------------- escalation queue
def escalate(role, question, context=""):
    """A judgment call the local model is forbidden to make. Resolved by frontier."""
    con = connect()
    cur = con.execute("INSERT INTO messages(sender,recipient,topic,body,created_at) "
                      "VALUES (?,?,?,?,?)",
                      (role, "ESCALATION", "judgment",
                       json.dumps({"role": role, "question": question, "context": context}),
                       now())); con.commit()
    return cur.lastrowid

def pending_escalations():
    con = connect()
    return [dict(r) for r in con.execute(
        "SELECT * FROM messages WHERE recipient='ESCALATION' AND consumed_at IS NULL ORDER BY id")]

def proposer(summary, falsifier, action_class="GREEN", payload=None, evidence_ids=None):
    """Proposals REQUIRE a falsifier - how we would know this failed."""
    if not falsifier or not falsifier.strip():
        raise ValueError("Proposal rejected unread: no falsifier supplied.")
    guard.check_action("propose", "GREEN")
    con = connect()
    cur = con.execute("INSERT INTO proposals(action_class,summary,payload,falsifier,"
                      "evidence_ids,agent,created_at) VALUES (?,?,?,?,?,?,?)",
                      (action_class, summary, json.dumps(payload or {}), falsifier,
                       json.dumps(evidence_ids or []), "proposer", now())); con.commit()
    pid = cur.lastrowid
    send("proposer", f"proposal #{pid}: {summary}", topic="new_proposal")
    return pid

def verifier(proposal_id):
    """Gathers facts INDEPENDENTLY. Must not read the proposer's reasoning first
    (anti-anchoring, DECISION_PROTOCOL.md section 3)."""
    con = connect()
    p = con.execute("SELECT summary, falsifier FROM proposals WHERE id=?",
                    (proposal_id,)).fetchone()
    if not p: raise ValueError(f"no proposal {proposal_id}")
    return {"proposal_id": proposal_id, "claim_to_check": p["summary"],
            "note": "Verifier sees the CLAIM only, never the proposer's argument."}

def adversary(proposal_id, argument, severity="concern", evidence_ids=None):
    """Its win condition is finding the flaw, not agreeing."""
    con = connect()
    cur = con.execute("INSERT INTO objections(proposal_id,agent,severity,argument,"
                      "evidence_ids,created_at) VALUES (?,?,?,?,?,?)",
                      (proposal_id, "adversary", severity, argument,
                       json.dumps(evidence_ids or []), now())); con.commit()
    return cur.lastrowid

def judge(proposal_id, decision, reasoning, addressed_objection_id, model_used):
    """A ruling that ignores the strongest objection is VOID."""
    con = connect()
    objs = con.execute("SELECT id FROM objections WHERE proposal_id=? AND severity='blocking'",
                       (proposal_id,)).fetchall()
    if objs and decision == "approve":
        raise ValueError(f"VETO: proposal #{proposal_id} has a blocking objection. "
                         f"Majority does not carry on blocked proposals.")
    if addressed_objection_id is None and con.execute(
            "SELECT COUNT(*) FROM objections WHERE proposal_id=?", (proposal_id,)).fetchone()[0]:
        raise ValueError("Ruling VOID: objections exist but none was addressed.")
    con.execute("INSERT INTO rulings(proposal_id,decision,addressed_objection_id,reasoning,"
                "model_used,created_at) VALUES (?,?,?,?,?,?)",
                (proposal_id, decision, addressed_objection_id, reasoning, model_used, now()))
    con.execute("UPDATE proposals SET status=? WHERE id=?",
                ("approved" if decision == "approve" else "rejected", proposal_id))
    con.commit()

