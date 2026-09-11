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

from core.launch import background      # один способ запуска на всю систему

# НИ ОДИН ДОЧЕРНИЙ ПРОЦЕСС НЕ ОТКРЫВАЕТ ОКНО.
# Окна выскакивали не из запуска воркера, а из КАЖДОГО вызова gh, git, node и
# powershell: процесс без собственной консоли заводит новое окно на каждый
# такой вызов. Их двадцать, и правка по местам гарантировала бы двадцать
# первый. Флаг ставится один раз на весь процесс.
try:
    from core.launch import silence as _silence
    _silence()
except Exception:
    pass



def running(needle):
    """Сколько процессов python содержат в командной строке эту подстроку."""
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         # Считаем ТОЛЬКО интерпретаторы. py.exe и pyw.exe — запускатели: они
         # порождают python.exe/pythonw.exe и живут рядом, поэтому один воркер
         # виден как два процесса. Именно на этом сторож и решал, что есть
         # дубль, убивал «лишнего» и запускал нового — по кругу.
         "(Get-CimInstance Win32_Process -Filter \"Name='python.exe' "
         "OR Name='pythonw.exe' OR Name='node.exe'\" | "
         "Where-Object { $_.CommandLine -like '*" + needle + "*' } "
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
    background(["python", "agents/worker.py", str(interval)],
               cwd=ROOT, log=ROOT / "data" / "worker.log")


def start_service():
    background(["node", "server.js"], cwd=ROOT / "service",
               log=ROOT / "data" / "server.log")


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
                        "Get-CimInstance Win32_Process -Filter \"Name='py.exe' OR Name='pyw.exe' "
                        "OR Name='python.exe' OR Name='pythonw.exe'\" | "
                        "Where-Object { $_.CommandLine -like '*worker.py*' } | "
                        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"], capture_output=True, timeout=90)
        start_worker()
        acted.append(f"воркер перезапущен (процесс висел, но журнал молчал "
                     f"{int(age) if age else '—'} сек)")
    elif procs > 1:
        # Два воркера — это не «к сведению», а поломка: они пишут в одну базу
        # и создают ровно те блокировки, от которых система умирала. Оставляем
        # один, лишние снимаем.
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        "Get-CimInstance Win32_Process -Filter \"Name='py.exe' OR Name='pyw.exe' "
                        "OR Name='python.exe' OR Name='pythonw.exe'\" | "
                        "Where-Object { $_.CommandLine -like '*worker.py*' } | "
                        "Sort-Object CreationDate | Select-Object -Skip 1 | "
                        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"], capture_output=True, timeout=90)
        acted.append(f"лишних воркеров снято: {procs - 1} (двойная запись в базу "
                     f"создаёт те самые блокировки)")

    if not service_alive():
        start_service()
        acted.append("сервис поднят (не отвечал на /health)")

    return acted


def install():
    """Сторож раз в пять минут и при входе в систему."""
    task = "P0-keep-alive"
    # Запуск ЧЕРЕЗ СКРЫТЫЙ ЗАПУСКАТЕЛЬ. Планировщик показывает чёрное окно
    # консоли при каждом срабатывании; раз в пять минут это помеха на рабочем
    # столе, а не признак работы.
    cmd = f'wscript.exe "{ROOT / "ops" / "run_hidden.vbs"}" "{ROOT / "keep_alive.py"}"'
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
