"""Opt-in hosted inference; requires a verified Workers Free account.

No local fallback, automatic retries, model switching, or billing upgrade.
Account authorization and plan verification must precede activation.
"""
import json
import os
import re
import urllib.error
import urllib.request

MODEL = "@cf/meta/llama-3.2-3b-instruct"

class CloudModelUnavailable(RuntimeError):
    pass


def generate(prompt, timeout=60, structured=False):
    if os.environ.get("P0_CLOUD_FREE_VERIFIED") != "1":
        raise CloudModelUnavailable("Cloud activation requires verified Free plan")
    account = os.environ.get("P0_CLOUDFLARE_ACCOUNT_ID", "")
    token = os.environ.get("P0_CLOUDFLARE_AI_TOKEN", "")
    if not re.fullmatch(r"[a-fA-F0-9]{32}", account) or not token:
        raise CloudModelUnavailable("Missing cloud account or AI credential")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 24000:
        raise CloudModelUnavailable("Prompt must contain 1..24000 characters")
    payload = {"messages": [{"role": "user", "content": prompt}],
               "max_tokens": 512, "temperature": 0.2, "stream": False}
    if structured:
        payload["response_format"] = {"type": "json_object"}
    req = urllib.request.Request(
        f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{MODEL}",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=min(timeout, 90)) as response:
            raw = response.read(262145)
        if len(raw) > 262144:
            raise CloudModelUnavailable("Cloud response exceeded size limit")
        data = json.loads(raw)
    except urllib.error.HTTPError as error:
        # Provider bodies may contain sensitive request information.
        raise CloudModelUnavailable(f"Cloud inference HTTP {error.code}; no retry") from None
    except (urllib.error.URLError, TimeoutError, ValueError):
        raise CloudModelUnavailable("Cloud inference unavailable or invalid JSON") from None
    if not isinstance(data, dict) or data.get("success") is not True:
        raise CloudModelUnavailable("Cloud inference returned an unsuccessful response")
    result = data.get("result")
    text = result.get("response") if isinstance(result, dict) else None
    if structured and isinstance(text, dict):
        text = json.dumps(text, ensure_ascii=False)
    if not isinstance(text, str) or not text.strip():
        raise CloudModelUnavailable("Cloud inference returned no generated text")
    return text
