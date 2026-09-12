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


def _pids(pattern, names):
    """Номера процессов нужного вида, чья командная строка содержит образец.

    ВИД ПРОЦЕССА ОБЯЗАТЕЛЕН. Без него поиск находил САМ СЕБЯ: в командной
    строке запроса PowerShell стоит тот же образец, который мы ищем, и пульт
    решал, что служба уже работает, — после чего не запускал её и докладывал
    «остановлена». Проверка, считающая собственное отражение за находку,
    выглядит работающей ровно до первого решения на её основе.
    """
    cond = " -or ".join(f"$_.Name -eq '{n}'" for n in names)
    out = _ps(f"(Get-CimInstance Win32_Process | Where-Object {{({cond}) -and "
              f"$_.CommandLine -like '*{pattern}*'}}).ProcessId")
    return [p.strip() for p in out.split() if p.strip().isdigit()]


def _server_pids(port=PORT):
    """Процесс службы — тот, кто СЛУШАЕТ наш порт, а не тот, чьё имя похоже.

    Прежний поиск искал «server.js» в командной строке и при первой же
    настоящей остановке снял вместе с дашбордом два чужих процесса: у
    MCP-сервера @magicuidesign/mcp файл называется так же. Выключатель,
    который гасит чужие программы, хуже отсутствия выключателя. Порт
    принадлежит ровно одному процессу, и ошибиться по нему нельзя.
    """
    out = _ps(f"(Get-NetTCPConnection -LocalPort {port} -State Listen "
              f"-ErrorAction SilentlyContinue).OwningProcess | Sort-Object -Unique")
    return [x.strip() for x in out.split() if x.strip().isdigit() and x.strip() != "0"]


PY_NAMES = ("pythonw.exe", "python.exe", "py.exe", "pyw.exe")
NODE_NAMES = ("node.exe",)


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
    worker = _pids("agents/worker.py", PY_NAMES)
    server = _server_pids()
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


# МЕТКА ВЫКЛЮЧАТЕЛЯ. Пульт кладёт KILL_SWITCH при остановке и снимает при
# пуске — но только СВОЙ. Если владелец положил его сам, как аварийный тормоз,
# «пуск» его не тронет: чужое решение остановиться пульт не отменяет.
SWITCH_MARK = "поставлен пультом p0.py"


def _kill_switch_path():
    from core import guard
    return guard.KILL_SWITCH


def _unload_models():
    """Выгружает локальные модели из памяти. Сам Ollama не трогает.

    Мешает работе не процесс Ollama — в простое он занимает десятки мегабайт, —
    а загруженная модель: qwen3-coder:30b держит в памяти около восемнадцати
    гигабайт. Ollama стоит в автозагрузке и может быть нужен владельцу для
    чего-то своего, поэтому выключатель освобождает память, а не убивает
    чужую программу.
    """
    import json as _json
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/ps", timeout=5) as r:
            models = [m["name"] for m in _json.load(r).get("models", [])]
    except (urllib.error.URLError, OSError, ValueError):
        return None
    for name in models:
        body = _json.dumps({"model": name, "keep_alive": 0}).encode()
        try:
            urllib.request.urlopen(urllib.request.Request(
                "http://127.0.0.1:11434/api/generate", data=body,
                headers={"Content-Type": "application/json"}), timeout=30).read()
        except (urllib.error.URLError, OSError):
            pass
    return models


def stop():
    print("останавливаю домашнюю часть...")
    for t in TASKS:
        _ps(f"Disable-ScheduledTask -TaskName '{t}' -ErrorAction SilentlyContinue | Out-Null")
        print(f"  задача {t}: выключена")
    # ГОНКА, КОТОРУЮ НАДО БЫЛО ЗАКРЫТЬ. Сторож, запущенный планировщиком за
    # секунду до отключения задачи, успевал поднять воркер уже после нашей
    # остановки. Поэтому сначала снимаются сами сторож и аудит, а KILL_SWITCH
    # ставится ДО остановки воркера: если что-то всё же поднимет его, он
    # остановится на первом же обороте.
    for pat in ("keep_alive.py", "hourly_audit.py"):
        for pid in _pids(pat, PY_NAMES):
            subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True,
                           creationflags=0x08000000)
    ks = _kill_switch_path()
    if not ks.exists():
        ks.write_text(SWITCH_MARK + "\n", encoding="utf-8")
    for label, pids in (("воркер", _pids("agents/worker.py", PY_NAMES)),
                        ("служба", _server_pids())):
        for pid in pids:
            subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True,
                           creationflags=0x08000000)
        print(f"  {label}: {'остановлен, № ' + ', '.join(pids) if pids else 'уже не работал'}")
    models = _unload_models()
    if models is None:
        print("  локальные модели: Ollama не отвечает — выгружать нечего")
    else:
        print(f"  локальные модели: выгружено из памяти {len(models)}"
              + (f" ({', '.join(models)})" if models else ""))
    print("\nдомашняя часть выключена.")
    print("ОБЛАКО ПРОДОЛЖАЕТ РАБОТАТЬ — прогон каждые 15 минут.")
    print("Чтобы остановить и его: py -3.13 -X utf8 p0.py облако-стоп")
    return 0


def start():
    print("включаю домашнюю часть...")
    ks = _kill_switch_path()
    if ks.exists():
        if SWITCH_MARK in ks.read_text(encoding="utf-8", errors="ignore"):
            ks.unlink()
        else:
            print("  KILL_SWITCH положен НЕ пультом — это чьё-то решение остановиться.")
            print(f"  Пуск отменён. Если остановка больше не нужна, удалите файл: {ks}")
            return 1
    for t in TASKS:
        _ps(f"Enable-ScheduledTask -TaskName '{t}' -ErrorAction SilentlyContinue | Out-Null")
        print(f"  задача {t}: включена")
    if not _server_pids():
        background(["node", "server.js"], cwd=ROOT / "service",
                   log=str(ROOT / "data" / "server.log"))
        print("  служба: запущена")
    if not _pids("agents/worker.py", PY_NAMES):
        background(["python", "agents/worker.py", "60"], cwd=ROOT,
                   log=str(ROOT / "data" / "worker.log"))
        print("  воркер: запущен")
    if not _ollama():
        try:
            background(["ollama", "serve"], cwd=ROOT, log=str(ROOT / "data" / "ollama.log"))
            print("  локальные модели: Ollama запущен")
        except OSError:
            print("  локальные модели: Ollama не найден — мышление уйдёт в облако")
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


def menu():
    """Для ярлыка на рабочем столе: показать состояние и спросить, что сделать."""
    status()
    print()
    print('  1 — ВЫКЛЮЧИТЬ (агенты, дашборд, сторож; память моделей освобождается)')
    print('  2 — ВКЛЮЧИТЬ обратно')
    print('  Enter — ничего не менять')
    try:
        ans = input('\nваш выбор: ').strip()
    except EOFError:
        return 0
    if ans == '1':
        return stop()
    if ans == '2':
        return start()
    print('ничего не изменено')
    return 0


def toggle():
    """Одна кнопка: работает — выключить, стоит — включить.

    Работающим считается то, что работает на деле: живой воркер или хотя бы
    одна включённая задача планировщика, которая поднимет его через пять минут.
    """
    enabled = any(_task_state(t) in ("Ready", "Running") for t in TASKS)
    if _pids("agents/worker.py", PY_NAMES) or enabled:
        return stop()
    return start()


CMDS = {"": status, "статус": status, "status": status,
        "переключить": toggle, "toggle": toggle, "menu": menu, "меню": menu,
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
