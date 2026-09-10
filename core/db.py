"""P0 spine. The database IS the asset - every external platform is a replaceable pipe."""
import sqlite3, os, json, time
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "brain.db"

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- ============ EVIDENCE LAYER: no source row, no vote ============
CREATE TABLE IF NOT EXISTS sources (
  id INTEGER PRIMARY KEY,
  url TEXT NOT NULL,
  title TEXT,
  fetched_at TEXT NOT NULL,
  content_hash TEXT,
  raw_excerpt TEXT
);
CREATE TABLE IF NOT EXISTS evidence (
  id INTEGER PRIMARY KEY,
  claim TEXT NOT NULL,
  source_id INTEGER NOT NULL REFERENCES sources(id),
  agent TEXT NOT NULL,
  created_at TEXT NOT NULL,
  confidence REAL,
  disputed_by TEXT
);

-- ============ CANDIDATES + RUBRIC ============
CREATE TABLE IF NOT EXISTS candidates (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  model TEXT,                 -- business model description
  status TEXT NOT NULL DEFAULT 'open',   -- open | eliminated | shortlisted | chosen
  eliminated_by_gate TEXT,    -- G1..G6 that killed it
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS scores (
  id INTEGER PRIMARY KEY,
  candidate_id INTEGER NOT NULL REFERENCES candidates(id),
  criterion TEXT NOT NULL,
  value REAL,
  evidence_ids TEXT NOT NULL,  -- JSON list; empty = INVALID score
  agent TEXT NOT NULL,
  created_at TEXT NOT NULL
);

-- ============ COUNCIL (DECISION_PROTOCOL.md) ============
CREATE TABLE IF NOT EXISTS proposals (
  id INTEGER PRIMARY KEY,
  action_class TEXT NOT NULL,        -- GREEN | YELLOW | RED | BLACK
  summary TEXT NOT NULL,
  payload TEXT,
  falsifier TEXT NOT NULL,           -- "how we would know this failed" - required
  evidence_ids TEXT,
  agent TEXT NOT NULL,
  created_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'proposed'  -- proposed|vetoed|approved|executed|rejected
);
CREATE TABLE IF NOT EXISTS objections (
  id INTEGER PRIMARY KEY,
  proposal_id INTEGER NOT NULL REFERENCES proposals(id),
  agent TEXT NOT NULL,
  severity TEXT NOT NULL,            -- blocking | concern
  argument TEXT NOT NULL,
  evidence_ids TEXT,
  created_at TEXT NOT NULL,
  proved_correct INTEGER              -- filled later from outcomes -> reputation
);
CREATE TABLE IF NOT EXISTS rulings (
  id INTEGER PRIMARY KEY,
  proposal_id INTEGER NOT NULL REFERENCES proposals(id),
  decision TEXT NOT NULL,            -- approve | reject | defer_to_owner
  addressed_objection_id INTEGER REFERENCES objections(id),  -- must address strongest
  reasoning TEXT NOT NULL,
  model_used TEXT NOT NULL,
  created_at TEXT NOT NULL
);

-- ============ AGENT BUS (agents talk here - free, local) ============
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY,
  sender TEXT NOT NULL,
  recipient TEXT,                    -- NULL = broadcast
  topic TEXT,
  body TEXT NOT NULL,
  created_at TEXT NOT NULL,
  consumed_at TEXT
);

-- ============ ACTIONS + BLAST RADIUS ============
CREATE TABLE IF NOT EXISTS actions (
  id INTEGER PRIMARY KEY,
  proposal_id INTEGER REFERENCES proposals(id),
  kind TEXT NOT NULL,
  action_class TEXT NOT NULL,
  dry_run INTEGER NOT NULL DEFAULT 1,
  payload TEXT,
  result TEXT,
  created_at TEXT NOT NULL
);

-- ============ THE ASSET: our own list copy ============
CREATE TABLE IF NOT EXISTS subscribers (
  id INTEGER PRIMARY KEY,
  email TEXT NOT NULL UNIQUE,
  esp_id TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  source TEXT,
  consent_proof TEXT,
  segment TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS email_events (
  id INTEGER PRIMARY KEY,
  subscriber_id INTEGER REFERENCES subscribers(id),
  campaign TEXT,
  event TEXT NOT NULL,               -- sent|open|click|bounce|unsub
  occurred_at TEXT NOT NULL,
  meta TEXT
);

-- ============ GROUND TRUTH: revenue ============
CREATE TABLE IF NOT EXISTS payments (
  id INTEGER PRIMARY KEY,
  chain TEXT NOT NULL,
  tx_hash TEXT NOT NULL UNIQUE,
  amount TEXT NOT NULL,
  asset TEXT NOT NULL,
  received_at TEXT NOT NULL,
  attributed_to TEXT
);

-- ============ LEARNING: agent reputation from OUTCOMES only ============
CREATE TABLE IF NOT EXISTS agent_reputation (
  id INTEGER PRIMARY KEY,
  agent TEXT NOT NULL,
  role TEXT NOT NULL,
  calls INTEGER NOT NULL DEFAULT 0,
  correct INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL,
  UNIQUE(agent, role)
);

CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY,
  agent TEXT NOT NULL,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  status TEXT,
  notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_msg_unconsumed ON messages(recipient, consumed_at);
CREATE INDEX IF NOT EXISTS idx_ev_source ON evidence(source_id);
"""

def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def init():
    con = connect()
    con.executescript(SCHEMA)
    con.commit()
    return con

if __name__ == "__main__":
    con = init()
    tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    print(f"brain.db ready at {DB_PATH}")
    print(f"{len(tables)} tables: {', '.join(tables)}")
