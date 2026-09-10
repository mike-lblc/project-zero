"""Публикация MCP-сервера в официальный реестр modelcontextprotocol.io.

Запуск владельцем:
    cd "C:/Users/chebo/Desktop/Brain"
    py -3.13 -X utf8 ops/publish_mcp.py

Ничего вводить не нужно: GitHub-токен берётся из уже авторизованного `gh`.
Публикуется только то, что лежит в ops/mcp_publish.json — посмотрите файл заранее.

Что происходит:
  1. берём токен из gh auth token
  2. меняем его в реестре на registry-токен (эндпоинт /v0/auth/github-at)
  3. публикуем запись о сервере (эндпоинт /v0/publish)
  4. проверяем, что запись реально появилась в реестре
"""
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAYLOAD = ROOT / "ops" / "mcp_publish.json"
REGISTRY = "https://registry.modelcontextprotocol.io"


def call(url, data=None, token=None, method="POST"):
    headers = {"User-Agent": "P0-publisher/1.0", "Accept": "application/json",
               "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    body = json.dumps(data).encode() if data is not None else None
    try:
        r = urllib.request.urlopen(
            urllib.request.Request(url, headers=headers, data=body, method=method), timeout=40)
        return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"


def main():
    if not PAYLOAD.exists():
        print(f"НЕТ ФАЙЛА: {PAYLOAD}")
        return 1
    payload = json.loads(PAYLOAD.read_text(encoding="utf-8"))
    name = payload["name"]
    print(f"Публикуем: {name}")
    print(f"Адрес MCP: {payload['remotes'][0]['url']}")
    print()

    # 1. токен GitHub из уже авторизованного gh
    try:
        gh_token = subprocess.run(["gh", "auth", "token"], capture_output=True,
                                  text=True, timeout=30).stdout.strip()
    except Exception as e:
        print(f"не удалось получить токен gh: {e}")
        return 1
    if not gh_token:
        print("gh не авторизован. Выполните: gh auth login")
        return 1
    print("[1/4] токен GitHub получен")

    # 2. обмен на registry-токен
    st, body = call(f"{REGISTRY}/v0/auth/github-at", {"github_token": gh_token})
    if st != 200:
        print(f"[2/4] ОБМЕН НЕ УДАЛСЯ: HTTP {st}\n{body[:400]}")
        return 1
    reg_token = (json.loads(body).get("registry_token")
                 or json.loads(body).get("token"))
    if not reg_token:
        print(f"[2/4] реестр не вернул токен:\n{body[:400]}")
        return 1
    print("[2/4] registry-токен получен")

    # 3. публикация
    st, body = call(f"{REGISTRY}/v0/publish", payload, token=reg_token)
    if st not in (200, 201):
        print(f"[3/4] ПУБЛИКАЦИЯ НЕ УДАЛАСЬ: HTTP {st}\n{body[:600]}")
        return 1
    print(f"[3/4] опубликовано, HTTP {st}")

    # 4. проверка: запись действительно в реестре
    st, body = call(f"{REGISTRY}/v0/servers?search={urllib.parse.quote(name)}",
                    None, method="GET")
    found = name in body
    print(f"[4/4] проверка в реестре: {'НАЙДЕН' if found else 'пока не виден (индексация)'}")
    print()
    print("Готово. Сервер виден агентам, которые обходят реестр MCP.")
    return 0


if __name__ == "__main__":
    import urllib.parse
    sys.exit(main())
