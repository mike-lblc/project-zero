"""СНИМОК ВЫДАЧИ ДЛЯ ОБЛАЧНОГО ДАШБОРДА.

Дашборд с трёхмерной сценой живёт на машине владельца и по сети недоступен.
В облаке доступны только файлы, поэтому здесь снимаются те же пять выдач,
которыми питается живой дашборд, и кладутся рядом со страницей.

ПОЧЕМУ ЧЕРЕЗ ЖИВОЙ СЕРВЕР, А НЕ ПОВТОРНЫМ ЗАПРОСОМ К БАЗЕ. Повторить здесь
запросы из server.js значило бы завести вторую реализацию тех же цифр. Две
реализации расходятся всегда — сначала по мелочи, потом по существу, и в
какой-то день дашборд в облаке начинает показывать другое число, чем дома,
причём обе стороны уверены в своей правоте. Поэтому снимок снимается с ТОГО
ЖЕ сервера: он поднимается, отвечает, выключается.

Каждый файл помечается временем съёмки. Страница читает эту метку и пишет
«снимок облака от такого-то», а не «живой поток»: снимок, выданный за живые
данные, — это вчерашнее состояние под видом сегодняшнего.
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8402
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "api"

# Ровно то, что просит дашборд. Список короткий намеренно: лишняя выдача в
# снимке — это данные, которые никто не показывает, но все обязаны обновлять.
ENDPOINTS = ("status", "execution", "queue", "chat", "pulse")


def alive(port, tries=1):
    for _ in range(tries):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=3)
            return True
        except (urllib.error.URLError, OSError):
            time.sleep(1)
    return False


def main():
    started = None
    if not alive(PORT):
        print(f"сервер на :{PORT} не отвечает — поднимаю свой")
        log = ROOT / "data" / "server_export.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        output = open(log, "a", encoding="utf-8", errors="ignore")
        env = os.environ.copy()
        env["PORT"] = str(PORT)
        # В CI нужен обычный node-процесс. Общий background() специально
        # подставляет pythonw на Windows и закрывает дескрипторы; на временном
        # GitHub runner это лишнее и скрывало ранний выход процесса.
        started = subprocess.Popen(
            ["node", "server.js"], cwd=ROOT / "service", env=env,
            stdout=output, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        )
        if not alive(PORT, tries=20):
            # Причину НАДО ПОКАЗАТЬ. Отказ без причины отправляет разбираться
            # заново того, кто увидит его следующим, — а журнал к тому моменту
            # уже стёрт вместе с машиной, на которой всё это крутилось.
            print("СБОЙ: сервер не поднялся, снимок не снят", file=sys.stderr)
            log = ROOT / "data" / "server_export.log"
            if log.exists():
                tail = log.read_text(encoding="utf-8", errors="ignore").splitlines()[-25:]
                print("— что сказал сервер —", file=sys.stderr)
                for ln in tail:
                    print("   " + ln, file=sys.stderr)
            else:
                print(f"журнала {log} нет — процесс не дошёл до запуска", file=sys.stderr)
            if started:
                started.terminate()
            output.close()
            return 1

    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).isoformat()
    ok = 0
    for name in ENDPOINTS:
        try:
            r = urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/{name}", timeout=25)
            data = json.loads(r.read().decode("utf-8", "ignore"))
        except Exception as e:
            # Молчаливого пропуска здесь быть не может: страница покажет
            # пустоту и будет выглядеть исправной.
            print(f"  СБОЙ {name}: {type(e).__name__}: {str(e)[:90]}", file=sys.stderr)
            continue
        if isinstance(data, dict):
            data["__snapshot_at"] = stamp
        (OUT / f"{name}.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8")
        size = (OUT / f"{name}.json").stat().st_size
        print(f"  {name}.json — {size:,} байт")
        ok += 1

    if started:
        started.terminate()
        try:
            started.wait(timeout=5)
        except subprocess.TimeoutExpired:
            started.kill()
            started.wait(timeout=5)
        output.close()

    print(f"снято выдач: {ok} из {len(ENDPOINTS)}")
    return 0 if ok == len(ENDPOINTS) else 1


if __name__ == "__main__":
    sys.exit(main())
