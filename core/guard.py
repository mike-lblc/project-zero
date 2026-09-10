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

class Halted(Exception): pass
class CapExceeded(Exception): pass
class Forbidden(Exception): pass

def check_alive():
    if KILL_SWITCH.exists():
        raise Halted("KILL_SWITCH present - all agent action halted.")

def check_action(kind, action_class):
    check_alive()
    if action_class == "BLACK":
        raise Forbidden(f"'{kind}' is BLACK: agents may never do this (accounts, "
                        f"KYC, CAPTCHA, spending, moving funds).")
    if action_class not in CLASSES:
        raise Forbidden(f"unknown action class {action_class!r}")
    cap = CAPS.get(kind)
    if cap is not None:
        con = connect()
        n = con.execute(
            "SELECT COUNT(*) FROM actions WHERE kind=? AND dry_run=0 AND date(created_at)=?",
            (kind, date.today().isoformat())).fetchone()[0]
        if n >= cap:
            raise CapExceeded(f"'{kind}' hit today's cap ({cap}). Agents cannot exceed this.")
    return True
