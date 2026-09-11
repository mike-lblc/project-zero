"""ПУЛЬТ УПРАВЛЕНИЯ — включить, выключить, посмотреть.

Владелец спросил прямо: если большая часть работает на моей машине, как мне
это включать и выключать. До сих пор ответа не было — процессы поднимались
сторожем жизни и планировщиком Windows, и чтобы их остановить, надо было
знать имена задач и номера процессов. Система, которую нельзя выключить одной
командой, управляет владельцем, а не наоборот.

    py -3.13 -X utf8 p0.py                 что сейчас работает
    py -3.13 -X utf8 p0.py стоп            выключить ВСЁ на этой машине
    py -3.13 -X utf8 p0.py пуск            включить обратно
    py -3.13 -X utf8 p0.py облако-стоп     остановить и облачные прогоны
    py -3.13 -X utf8 p0.py облако-пуск     вернуть облачные прогоны

ЧТО ВАЖНО ЗНАТЬ ПРО «СТОП». Он останавливает только домашнюю часть. Облако
продолжит работать: там свой прогон каждые пятнадцать минут, и он не зависит
от того, включён ли ваш ноутбук — в этом и был весь смысл переноса. Чтобы
замолчало всё, нужны обе команды, и здесь это сказано прямо, а не оставлено
на догадку.
"""
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from core.launch import background, silence  # noqa: E402

silence()

TASKS = ["P0-keep-alive", "P0-hourly-audit"]
PORT = 8402


def _ps(cmd):
    r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="ignore", creationflags=0x08000000)
    return r.stdout.strip()


def _pids(pattern):
    """Номера процессов, чья командная строка содержит образец."""
    out = _ps(f"(Get-CimInstance Win32_Process | Where-Object "
              f"{{$_.CommandLine -like '*{pattern}*'}}).ProcessId")
    return [p.strip() for p in out.split() if p.strip().isdigit()]


def _alive(port=PORT):
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=3)
        return True
    except (urllib.error.URLError, OSError):
        return False


def _ollama():
    try:
        urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=3)
        return True
    except (urllib.error.URLError, OSError):
        return False


def _task_state(name):
    s = _ps(f"(Get-ScheduledTask -TaskName '{name}' -ErrorAction SilentlyContinue).State")
    return s or "нет такой задачи"


def _cloud_state():
    """Включён ли облачный прогон. Спрашивается у GitHub, а не предполагается."""
    r = subprocess.run(["gh", "workflow", "list", "--json", "name,state"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="ignore", cwd=str(ROOT), creationflags=0x08000000)
    if r.returncode != 0:
        return "неизвестно (gh не ответил)"
    return "включён" if '"active"' in (r.stdout or "").lower() else "выключен"


def status():
    worker = _pids("agents/worker.py")
    server = _pids("server.js")
    print("=" * 66)
    print("P0 — ЧТО СЕЙЧАС РАБОТАЕТ")
    print("=" * 66)
    print(f"  воркер (цикл агентов)   {'работает, № ' + ', '.join(worker) if worker else 'ОСТАНОВЛЕН'}")
    print(f"  служба и дашборд        {'отвечает на :' + str(PORT) if _alive() else 'ОСТАНОВЛЕНА'}")
    print(f"  локальные модели        {'отвечают' if _ollama() else 'НЕ ОТВЕЧАЮТ — мышление уйдёт в облако'}")
    for t in TASKS:
        print(f"  задача {t:<17} {_task_state(t)}")
    print(f"  облачный прогон         {_cloud_state()} (каждые 15 мин, ноутбук не нужен)")
    print()
    print("  выключить домашнюю часть:  py -3.13 -X utf8 p0.py стоп")
    print("  выключить и облако:        py -3.13 -X utf8 p0.py облако-стоп")
    return 0


def stop():
    print("останавливаю домашнюю часть...")
    for t in TASKS:
        _ps(f"Disable-ScheduledTask -TaskName '{t}' -ErrorAction SilentlyContinue | Out-Null")
        print(f"  задача {t}: выключена")
    for pat, label in (("agents/worker.py", "воркер"), ("server.js", "служба")):
        pids = _pids(pat)
        for pid in pids:
            subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True,
                           creationflags=0x08000000)
        print(f"  {label}: {'остановлен, № ' + ', '.join(pids) if pids else 'уже не работал'}")
    print("\nдомашняя часть выключена.")
    print("ОБЛАКО ПРОДОЛЖАЕТ РАБОТАТЬ — прогон каждые 15 минут.")
    print("Чтобы остановить и его: py -3.13 -X utf8 p0.py облако-стоп")
    return 0


def start():
    print("включаю домашнюю часть...")
    for t in TASKS:
        _ps(f"Enable-ScheduledTask -TaskName '{t}' -ErrorAction SilentlyContinue | Out-Null")
        print(f"  задача {t}: включена")
    if not _pids("server.js"):
        background(["node", "server.js"], cwd=ROOT / "service",
                   log=str(ROOT / "data" / "server.log"))
        print("  служба: запущена")
    if not _pids("agents/worker.py"):
        background(["python", "agents/worker.py", "60"], cwd=ROOT,
                   log=str(ROOT / "data" / "worker.log"))
        print("  воркер: запущен")
    time.sleep(7)
    print()
    return status()


def cloud(enable):
    """Включает или выключает облачный прогон целиком."""
    verb = "enable" if enable else "disable"
    r = subprocess.run(["gh", "workflow", verb, "agents.yml"],
                       capture_output=True, text=True, cwd=str(ROOT),
                       creationflags=0x08000000)
    ok = r.returncode == 0
    print(f"облачный прогон: {'включён' if enable else 'выключен'}" if ok
          else f"не получилось: {(r.stderr or '')[:160]}")
    if ok and not enable:
        print("Теперь молчит ВСЁ: и дом, и облако.")
    return 0 if ok else 1


CMDS = {"": status, "статус": status, "status": status,
        "стоп": stop, "stop": stop, "off": stop,
        "пуск": start, "start": start, "on": start,
        "облако-стоп": lambda: cloud(False), "cloud-off": lambda: cloud(False),
        "облако-пуск": lambda: cloud(True), "cloud-on": lambda: cloud(True)}

if __name__ == "__main__":
    cmd = (sys.argv[1] if len(sys.argv) > 1 else "").lower()
    fn = CMDS.get(cmd)
    if not fn:
        print(f"не знаю команду «{cmd}». Доступно: "
              f"{', '.join(sorted(set(['статус', 'стоп', 'пуск', 'облако-стоп', 'облако-пуск'])))}")
        sys.exit(2)
    sys.exit(fn())
