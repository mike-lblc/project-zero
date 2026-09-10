"""Model router. Enforces DECISION_PROTOCOL.md section 1:
THE LOCAL MODEL MAY NEVER DECIDE. Mechanical work runs locally; judgment escalates."""
import json, urllib.request, urllib.error

OLLAMA = "http://127.0.0.1:11434/api/generate"
LOCAL_MODEL = "qwen3.5:9b"      # 6.6GB - fits the 8GB VRAM. 4b was unreliable.

MECHANICAL = {"extract","classify","tag","format","parse","dedupe","summarize","translate"}
JUDGMENT   = {"decide","choose","plan","approve","rule","propose","evaluate","prioritize","pivot"}

class EscalationRequired(Exception):
    """Raised when a JUDGMENT task is sent to the local model. Never suppress."""

def _local(prompt, timeout=120):
    req = urllib.request.Request(OLLAMA,
        data=json.dumps({"model": LOCAL_MODEL, "prompt": prompt, "stream": False}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode()).get("response", "")

def run(task_type, prompt, _retry=True):
    """task_type must be in MECHANICAL. Anything else escalates by design."""
    t = task_type.lower().strip()
    if t in JUDGMENT or t not in MECHANICAL:
        raise EscalationRequired(
            f"'{task_type}' is a JUDGMENT task. The local model is forbidden to decide. "
            f"Escalate to a Claude Code subagent.")
    out = _local(prompt)
    # Known failure mode: small local models return empty. Retry once, then escalate.
    if not out or not out.strip():
        if _retry:
            out = _local(prompt)
        if not out or not out.strip():
            raise EscalationRequired(
                f"Local model returned empty twice for '{task_type}'. Escalating.")
    return out

def health():
    try:
        # Cold-start VRAM load can take minutes if another model must be evicted.
        return True, _local("Reply with the single word: ready", timeout=420).strip()[:60]
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
