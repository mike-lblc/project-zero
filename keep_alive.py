"""СТОРОЖ ЖИЗНИ — держит воркер и сервис поднятыми, чем бы их ни уронило.

Зачем понадобился. Воркер умирал раз за разом, и каждый раз по разной причине:
то падение внутри обработчика ошибок, то закрытие оболочки, из которой он был
запущен. Общее у всех случаев одно — процессом владел кто-то временный.

Здесь владельцем становится планировщик Windows. Сторож запускается раз в пять
минут, смотрит фактическое состояние и поднимает то, что лежит. Он идемпотентен:
если всё живо, он не делает ничего и ничего не пишет.

Два признака жизни, и оба фактические:
    ПРОЦЕСС ЕСТЬ     в системе висит python с agents/worker.py
    ЖУРНАЛ СВЕЖИЙ    последняя запись в runs моложе порога

Второй признак важнее первого. Процесс может висеть и не работать — так уже
было, когда воркер жил, а шаги не записывались. Живым считается тот, кто
оставляет следы, а не тот, кто присутствует в списке процессов.

Запуск разово:   py -3.13 -X utf8 keep_alive.py
Поставить:       py -3.13 -X utf8 keep_alive.py --install
"""
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from core.db import connect  # noqa: E402

PY = ["py", "-3.13", "-X", "utf8"]
STALE_SECONDS = 300          # журнал старше пяти минут — воркер не работает
SERVICE_URL = "http://127.0.0.1:8402/health"

# Windows: отвязать потомка от родителя, иначе он умрёт вместе с нами —
# ровно та причина, по которой воркер не переживал закрытие оболочки.
DETACHED = 0x00000008 | 0x00000200 if sys.platform == "win32" else 0


def running(needle):
    """Сколько процессов python содержат в командной строке эту подстроку."""
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "(Get-CimInstance Win32_Process -Filter \"Name='py.exe' OR Name='python.exe' "
         "OR Name='node.exe'\" | Where-Object { $_.CommandLine -like '*" + needle + "*' } "
         "| Measure-Object).Count"],
        capture_output=True, text=True, timeout=90)
    try:
        return int((r.stdout or "0").strip())
    except ValueError:
        return 0


def log_age():
    """Сколько секунд назад воркер оставил последний след. None — следов нет."""
    c = connect()
    try:
        last = c.execute("SELECT MAX(started_at) FROM runs").fetchone()[0]
    finally:
        c.close()
    if not last:
        return None
    t = last if ("+" in last[10:] or last.endswith("Z")) else last + "+00:00"
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(t)).total_seconds()
    except ValueError:
        return None


def start_worker(interval=60):
    log = open(ROOT / "data" / "worker.log", "a", encoding="utf-8")
    subprocess.Popen(PY + ["agents/worker.py", str(interval)], cwd=str(ROOT),
                     stdout=log, stderr=subprocess.STDOUT,
                     creationflags=DETACHED, close_fds=True)


def start_service():
    log = open(ROOT / "data" / "server.log", "a", encoding="utf-8")
    subprocess.Popen(["node", "server.js"], cwd=str(ROOT / "service"),
                     stdout=log, stderr=subprocess.STDOUT,
                     creationflags=DETACHED, close_fds=True)


def service_alive():
    import urllib.error
    import urllib.request
    try:
        urllib.request.urlopen(SERVICE_URL, timeout=10)
        return True
    except (urllib.error.URLError, OSError):
        return False


def check():
    acted = []

    procs = running("worker.py")
    age = log_age()
    stale = age is None or age > STALE_SECONDS

    if procs == 0:
        start_worker()
        acted.append(f"воркер поднят (процесса не было, последний след "
                     f"{int(age) if age else '—'} сек назад)")
    elif stale:
        # Процесс висит, но следов не оставляет — это хуже, чем мёртвый:
        # снаружи выглядит живым. Снимаем и поднимаем заново.
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        "Get-CimInstance Win32_Process -Filter \"Name='py.exe' OR "
                        "Name='python.exe'\" | Where-Object { $_.CommandLine -like "
                        "'*worker.py*' } | ForEach-Object { Stop-Process -Id "
                        "$_.ProcessId -Force }"], capture_output=True, timeout=90)
        start_worker()
        acted.append(f"воркер перезапущен (процесс висел, но журнал молчал "
                     f"{int(age) if age else '—'} сек)")
    elif procs > 1:
        acted.append(f"ВНИМАНИЕ: воркеров запущено {procs} — двойная нагрузка на базу")

    if not service_alive():
        start_service()
        acted.append("сервис поднят (не отвечал на /health)")

    return acted


def install():
    """Сторож раз в пять минут и при входе в систему."""
    task = "P0-keep-alive"
    cmd = f'py -3.13 -X utf8 "{ROOT / "keep_alive.py"}"'
    r = subprocess.run(["schtasks", "/Create", "/TN", task, "/SC", "MINUTE",
                        "/MO", "5", "/TR", cmd, "/F"],
                       capture_output=True, text=True, timeout=60)
    print(r.stdout or r.stderr)
    return r.returncode == 0


if __name__ == "__main__":
    if "--install" in sys.argv:
        sys.exit(0 if install() else 1)
    done = check()
    if done:
        for line in done:
            print(line)
    else:
        a = log_age()
        print(f"всё живо: воркер оставил след {int(a) if a else '—'} сек назад, "
              f"сервис отвечает")
