"""Отправка русского quickstart в BasedHardware/omi через форк.

Работаем через API, а не через клон: репозиторий большой, а изменений три файла.
"""
import base64
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FORK = "mike-lblc/omi"
UPSTREAM = "BasedHardware/omi"
BRANCH = "docs/russian-quickstart-omi-cli"
BASE = "main"


def gh(args, inp=None, timeout=90):
    r = subprocess.run(["gh"] + args, capture_output=True, text=True,
                       input=inp, timeout=timeout)
    if r.returncode != 0:
        print("  ! " + (r.stderr or "").strip()[:220])
        return None
    return r.stdout


def get_file(repo, path, ref=BASE):
    out = gh(["api", f"repos/{repo}/contents/{path}?ref={ref}"])
    if not out:
        return None, None
    d = json.loads(out)
    return base64.b64decode(d["content"]).decode("utf-8"), d["sha"]


def put_file(repo, path, content, message, branch, sha=None):
    payload = {"message": message, "branch": branch,
               "content": base64.b64encode(content.encode("utf-8")).decode()}
    if sha:
        payload["sha"] = sha
    return gh(["api", "-X", "PUT", f"repos/{repo}/contents/{path}",
               "--input", "-"], inp=json.dumps(payload))


def main():
    # 1. свежая ветка от upstream/main
    head = gh(["api", f"repos/{UPSTREAM}/git/ref/heads/{BASE}", "--jq", ".object.sha"])
    if not head:
        return 1
    head = head.strip()
    print(f"[1/5] upstream {BASE} = {head[:8]}")

    # синхронизируем форк, чтобы ветка отходила от актуального состояния
    gh(["api", "-X", "POST", f"repos/{FORK}/merge-upstream",
        "-f", f"branch={BASE}"])

    exists = gh(["api", f"repos/{FORK}/git/ref/heads/{BRANCH}"])
    if not exists:
        gh(["api", "-X", "POST", f"repos/{FORK}/git/refs",
            "-f", f"ref=refs/heads/{BRANCH}", "-f", f"sha={head}"])
        print(f"[2/5] ветка {BRANCH} создана")
    else:
        print(f"[2/5] ветка {BRANCH} уже есть")

    # 2. сам документ
    doc = (HERE / "quickstart.ru.md").read_text(encoding="utf-8")
    _, sha = get_file(FORK, "sdks/python-cli/examples/quickstart.ru.md", BRANCH)
    put_file(FORK, "sdks/python-cli/examples/quickstart.ru.md", doc,
             "docs(cli): add Russian quickstart for omi-cli", BRANCH, sha)
    print(f"[3/5] quickstart.ru.md добавлен ({len(doc)} символов)")

    # 3. перекрёстная ссылка в examples/README.md — в том же стиле, что у ja и es
    txt, sha = get_file(FORK, "sdks/python-cli/examples/README.md", BRANCH)
    if txt and "quickstart.ru.md" not in txt:
        anchor = ("* [`quickstart.es.md`](quickstart.es.md) — primeros pasos con omi-cli en\n"
                  "  español.\n")
        addition = ("* [`quickstart.ru.md`](quickstart.ru.md) — быстрый старт с omi-cli на\n"
                    "  русском (Russian Quickstart).\n")
        new = txt.replace(anchor, anchor + addition) if anchor in txt else txt.rstrip("\n") + "\n" + addition
        put_file(FORK, "sdks/python-cli/examples/README.md", new,
                 "docs(cli): link Russian quickstart from examples index", BRANCH, sha)
        print("[4/5] ссылка добавлена в examples/README.md")
    else:
        print("[4/5] examples/README.md уже содержит ссылку")

    # 4. перекрёстная ссылка в README самого CLI
    txt, sha = get_file(FORK, "sdks/python-cli/README.md", BRANCH)
    if txt and "quickstart.ru.md" not in txt:
        old = ("> Looking for localized guides? See the [🇯🇵 日本語クイックスタート "
               "(Japanese Quickstart)](examples/quickstart.ja.md) or the "
               "[🇪🇸 Primeros pasos con omi-cli (Spanish Quickstart)](examples/quickstart.es.md).")
        new_line = old[:-1] + (" or the [🇷🇺 Быстрый старт с omi-cli "
                               "(Russian Quickstart)](examples/quickstart.ru.md).")
        if old in txt:
            put_file(FORK, "sdks/python-cli/README.md", txt.replace(old, new_line),
                     "docs(cli): link Russian quickstart from CLI README", BRANCH, sha)
            print("[5/5] ссылка добавлена в README CLI")
        else:
            print("[5/5] строка со ссылками на языки не найдена — пропускаю")
    else:
        print("[5/5] README CLI уже содержит ссылку")

    print("\nВетка готова:", f"https://github.com/{FORK}/tree/{BRANCH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
