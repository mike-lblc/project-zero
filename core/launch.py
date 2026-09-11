"""ЗАПУСК ФОНОВЫХ ПРОЦЕССОВ — ровно одним способом, и без окон.

Почему это отдельный файл, а не флаг в четырёх местах. Окно консоли выскакивало
на рабочем столе владельца каждые несколько минут, я «починил» это дважды, и оба
раза не до конца: запусков оказалось ЧЕТЫРЕ, в разных файлах, с разными флагами.
Два вообще без флагов. Когда одно и то же делается в четырёх местах по-разному,
починка одного места создаёт уверенность, что починены все.

ЧТО ЗДЕСЬ ВАЖНО ПО СУЩЕСТВУ.

  pyw вместо py      — это оконный вариант того же запускателя Python, он не
                       выделяет консоль вовсе. Вывод пишем в файл, поэтому
                       ничего не теряется.
  CREATE_NO_WINDOW   — документированный способ запустить консольное
                       приложение без окна. Именно он, а не DETACHED_PROCESS:
                       последний означает «без родительской консоли», и для
                       части приложений Windows всё равно заводит новую.
  вывод в файл       — у оконного интерпретатора нет стандартного вывода, и
                       запись в него без перенаправления может уронить процесс.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ОКОННЫЙ ИНТЕРПРЕТАТОР НАПРЯМУЮ, БЕЗ ЗАПУСКАТЕЛЯ.
#
# «pyw» — это не интерпретатор, а запускатель: он ПОРОЖДАЕТ pythonw.exe и
# остаётся жить рядом. Один воркер в списке процессов выглядел как два, сторож
# считал это дублем, убивал «лишнего» и запускал новый — и так по кругу. За час
# накопилось восемь. Причина была не в гонке, как я решил сначала, а в том, что
# я считал два процесса одного воркера двумя воркерами.
#
# Прямой вызов pythonw.exe убирает посредника: один воркер — один процесс.
import sys as _sys
from pathlib import Path as _Path

_PYW_EXE = _Path(_sys.executable).with_name("pythonw.exe")
PYW = [str(_PYW_EXE)] if _PYW_EXE.exists() else ["pyw", "-3.13"]
PY = [_sys.executable]

# Документированный флаг «консольное приложение без окна».
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def background(args, cwd=None, log=None):
    """Запускает процесс в фоне БЕЗ ОКНА и возвращает его.

    args — список. Если первым идёт «python», подставляется оконный запускатель.
    log  — путь к файлу вывода; без него вывод отбрасывается.
    """
    cmd = list(args)
    if cmd and cmd[0] in ("python", "py", "pyw"):
        cmd = PYW + ["-X", "utf8"] + cmd[1:]

    if log:
        Path(log).parent.mkdir(parents=True, exist_ok=True)
        out = open(log, "a", encoding="utf-8", errors="ignore")
    else:
        out = subprocess.DEVNULL

    return subprocess.Popen(cmd, cwd=str(cwd or ROOT), stdout=out,
                            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                            creationflags=CREATE_NO_WINDOW, close_fds=True)


def run(args, timeout=120, cwd=None):
    """Разовый вызов с ожиданием результата — тоже без окна."""
    return subprocess.run(list(args), cwd=str(cwd or ROOT), capture_output=True,
                          text=True, timeout=timeout, encoding="utf-8",
                          errors="ignore", creationflags=CREATE_NO_WINDOW)


_SILENCED = [False]


def silence():
    """Ни один дочерний процесс больше не откроет окно. Одна точка на всё.

    ПОЧЕМУ ИМЕННО ТАК, А НЕ ПРАВКА ПО МЕСТАМ. Окно выскакивало не из четырёх
    запусков, которые я чинил, а из ДВАДЦАТИ обычных вызовов: каждое обращение
    к gh, git, node, powershell из процесса, у которого своей консоли нет,
    заводит новое окно. Пройтись по двадцати местам значит через неделю
    получить двадцать первое — новый вызов допишется без флага, и всё начнётся
    заново. Владелец уже трижды сообщал об этой проблеме, и дважды я «чинил»
    её частично.

    Поэтому флаг ставится ОДИН РАЗ на весь процесс: любой вызов, не указавший
    creationflags явно, получает CREATE_NO_WINDOW. Явно заданные флаги
    уважаются — эта заглушка добавляет, а не отбирает.
    """
    if _SILENCED[0] or sys.platform != "win32":
        return False
    _orig = subprocess.Popen.__init__

    def patched(self, *a, **kw):
        if not kw.get("creationflags"):
            kw["creationflags"] = CREATE_NO_WINDOW
        return _orig(self, *a, **kw)

    subprocess.Popen.__init__ = patched
    _SILENCED[0] = True
    return True
