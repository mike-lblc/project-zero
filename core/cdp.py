"""ДОСТУП К COINBASE DEVELOPER PLATFORM — подпись запросов и фасилитатор x402.

Зачем это нужно и что именно оно разблокирует. Индекс Bazaar, где агенты ищут
платные сервисы, ЧИТАЕТСЯ без ключа — так мы и собрали каталог на 14 тысяч
сервисов. А вот продавцовские эндпоинты (/supported, /verify, /settle) без
ключа отвечают 401. Это и было единственное, что упиралось в аккаунт.

Последовательность, которая ведёт к попаданию в индекс, взята из документации
CDP, а не придумана:
    1. эндпоинт отвечает 402 и отдаёт корректные метаданные;
    2. проходит ОДИН оплаченный вызов через фасилитатор CDP;
    3. дальше индексация происходит сама, примерно через полминуты.

КАК УСТРОЕНА ПОДПИСЬ. CDP не принимает ключ в заголовке: каждый запрос
подписывается коротким токеном JWT, действующим две минуты, и в него входит
КОНКРЕТНЫЙ метод и адрес. Токен, подписанный для одного запроса, не годится
для другого — это защищает от переиспользования перехваченного токена.

ЧЕГО ЗДЕСЬ НЕТ И НЕ БУДЕТ. Ни перевода средств, ни торговли, ни доступа к
балансу. Платежи x402 приходят прямо на кошелёк в сети Base и через счёт
Coinbase не проходят вовсе; фасилитатор только проверяет подписи платежей и
подтверждает расчёт. Права на распоряжение деньгами тут не нужны ни в каком
виде, и код, который бы ими воспользовался, писать нельзя.
"""
import base64
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HOST = "api.cdp.coinbase.com"
BASE = f"https://{HOST}"


class NoCredentials(Exception):
    """Ключ CDP не настроен. Это не сбой, а отсутствие настройки."""


def _env():
    """Читает .env. Ключи живут только там и никогда в коде."""
    out = {}
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, _, v = line.partition("=")
                out[k.strip()] = v.strip()
    out.setdefault("CDP_API_KEY_ID", os.environ.get("CDP_API_KEY_ID", ""))
    out.setdefault("CDP_API_KEY_SECRET", os.environ.get("CDP_API_KEY_SECRET", ""))
    return out


def have_credentials():
    e = _env()
    return bool(e.get("CDP_API_KEY_ID") and e.get("CDP_API_KEY_SECRET"))


def _b64u(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def make_jwt(method, path, ttl=120):
    """Токен для ОДНОГО запроса: метод и адрес входят в подпись.

    Ключ Ed25519 приходит из портала как 64 байта в base64: первые 32 — семя
    приватного ключа, вторые 32 — публичный. Подписываем семенем.
    """
    e = _env()
    kid = e.get("CDP_API_KEY_ID")
    sec = e.get("CDP_API_KEY_SECRET")
    if not kid or not sec:
        raise NoCredentials("нет CDP_API_KEY_ID или CDP_API_KEY_SECRET в .env")

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    raw = base64.b64decode(sec)
    if len(raw) not in (32, 64):
        raise ValueError(f"неожиданная длина ключа: {len(raw)} байт")
    key = Ed25519PrivateKey.from_private_bytes(raw[:32])

    now = int(time.time())
    header = {"alg": "EdDSA", "kid": kid, "typ": "JWT",
              "nonce": secrets.token_hex(16)}
    payload = {"sub": kid, "iss": "cdp", "aud": ["cdp_service"],
               "nbf": now, "exp": now + ttl,
               "uris": [f"{method.upper()} {HOST}{path}"]}
    signing_input = (_b64u(json.dumps(header, separators=(",", ":")).encode())
                     + "." + _b64u(json.dumps(payload, separators=(",", ":")).encode()))
    sig = key.sign(signing_input.encode())
    return signing_input + "." + _b64u(sig)


def call(method, path, body=None, timeout=30):
    """Запрос к CDP с подписью. Возвращает (код, разобранный ответ)."""
    token = make_jwt(method, path)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method.upper(),
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json",
                 "Accept": "application/json",
                 "User-Agent": "P0/1.0"})
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
        raw = r.read().decode("utf-8", "ignore")
        st = r.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "ignore")
        st = e.code
    except (urllib.error.URLError, OSError) as e:
        return 0, {"error": f"{type(e).__name__}: {str(e)[:120]}"}
    try:
        return st, json.loads(raw) if raw else {}
    except ValueError:
        return st, {"raw": raw[:400]}


# ────────────────────────────────────────────── проверки, пригодные для агентов
def supported():
    """Какие сети и схемы оплаты фасилитатор принимает. Это же и проверка ключа."""
    return call("GET", "/platform/v2/x402/supported")


def health():
    """Работает ли ключ. Возвращает (да/нет, что именно ответили).

    Отличает «ключа нет» от «ключ отвергнут» — это разные состояния, и путать
    их нельзя: первое чинится настройкой, второе означает недействительный ключ.
    """
    if not have_credentials():
        return False, "ключ CDP не настроен"
    st, d = supported()
    if st == 200:
        kinds = d.get("kinds") or d.get("supported") or []
        return True, f"ключ принят, поддерживаемых схем: {len(kinds)}"
    if st in (401, 403):
        return False, f"ключ ОТВЕРГНУТ ({st}): {str(d)[:120]}"
    return False, f"неожиданный ответ {st}: {str(d)[:120]}"


if __name__ == "__main__":
    print("ключ настроен:", have_credentials())
    ok, detail = health()
    print("проверка:", ok, "—", detail)
    if ok:
        st, d = supported()
        for k in (d.get("kinds") or [])[:8]:
            print("   ", k)
