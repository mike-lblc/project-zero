"""ГЛУБОКИЕ ПРОВЕРКИ — по образцам дефектов, которые уже случились.

Здесь не общие соображения о качестве кода, а поиск ПОВТОРЕНИЙ конкретных
поломок. Каждый образец ниже выведен из дефекта, который реально остановил
работу, и ищется он по всей системе — потому что одна и та же ошибка почти
никогда не живёт в одном месте.

Почему отдельный модуль, а не дополнение к ops/deep_audit.py: тот проверяет
целостность (синтаксис, тесты, база), а этот — повторяемость ошибок. Смешивать
их значит получить файл, который никто не читает целиком.

ГЛАВНОЕ ПРАВИЛО ОТЧЁТА. Находка без места в коде — это мнение. Каждая запись
ниже несёт файл и строку, иначе её нельзя ни проверить, ни починить.
"""
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SKIP = {".git", "node_modules", "__pycache__", ".venv", "work", "reports"}


def _py_files():
    return [p for p in ROOT.rglob("*.py")
            if not any(s in p.parts for s in SKIP)]


def _strip_comments(text):
    """Комментарии выбрасываются: в них мы цитируем починенные ошибки."""
    out = []
    for line in text.splitlines():
        q, cut, i = None, None, 0
        while i < len(line):
            ch = line[i]
            if q:
                if ch == "\\":
                    i += 2
                    continue
                if ch == q:
                    q = None
            elif ch in "\"'":
                q = ch
            elif ch == "#":
                cut = i
                break
            i += 1
        out.append(line[:cut] if cut is not None else line)
    return out


# ═══════════════════════════════════ ОБРАЗЦЫ УЖЕ СЛУЧИВШИХСЯ ПОЛОМОК
PATTERNS = [
    ("окно времени в чужом формате",
     r"[><]\s*datetime\('now'",
     "Метки хранятся с 'T' и смещением, а datetime('now') отдаёт их с пробелом: "
     "сравнение строк истинно ВСЕГДА, и окно «за сутки» молча возвращает всю "
     "историю. Проверено: «за час» давало 3892 прогона вместо 229."),

    ("пустой словарь принят за отсутствие",
     r"\b(headers|opts|options|params|kwargs|config)\s+or\s+\{",
     "Пустой словарь ложен, поэтому подставляется запасное значение, и код "
     "работает не с тем, что ему передали. Проверка «клиент без заголовка» на "
     "этом меряла не то, что заявляла, и вернула ложное «отказов нет»."),

    ("пустой список принят за отсутствие",
     r"\b(items|rows|paths|sources|files)\s+or\s+\[",
     "То же самое для списков: пустой список — это ОТВЕТ «ничего нет», а не "
     "отсутствие ответа. Подстановка запасного значения стирает разницу между "
     "«пусто» и «не спрашивали»."),

    ("устаревшее имя состояния задачи",
     r"state\s*=\s*'in_progress'|state='in_progress'",
     "Машина состояний пишет 'running'. Запросы с 'in_progress' не совпадали "
     "никогда: агент всегда видел пустой список своих задач."),

    ("поиск себя по старому адресу",
     r"\"trycloudflare\"|'trycloudflare'",
     "Служба давно на постоянном адресе. Проверка по имени временного туннеля "
     "не могла совпасть ни разу и докладывала «нас в индексе нет» всегда."),

    ("наличие строк выдано за работоспособность",
     r"COUNT\(\*\)[^)]*\)\s*>\s*0,\s*[\"'][^\"']*работает",
     "275 «передач работы» оказались одним сообщением, повторённым 275 раз. "
     "Проверка считала строки, а не разнообразие, и одобряла это."),

    ("молчаливая перезапись в реестре",
     r"^\s*(REGISTRY|TOOLS|HANDLERS)\[[^\]]+\]\s*=(?!=)",
     "Двое завели агента с одним именем; словарь оставил последнего, и половина "
     "работы исчезла без следа в журнале и проверках."),

    ("обрезка ответа без объявления",
     r"\.read\(\s*\d{4,7}\s*\)",
     "Чтение первых N байт отдаёт огрызок дальше по коду. Ответ на 8.8 МБ ломался "
     "на середине, и исправный источник выглядел сломанным."),

    # НЕ ЧИНИТСЯ АВТОМАТИЧЕСКИ. Отметка стоит здесь по горькому опыту: модель
    # получила эту находку и превратила except-pass в raise — то есть сделала
    # необязательное скрытие консольных окон УСЛОВИЕМ РАБОТЫ всего аудита.
    # Правка была формально верной по правилу и неверной по существу, а аудит
    # поймать её не мог: он меряет прохождение проверок, а не смысл.
    #
    # Причина глубже отметки. Чтобы починить проглоченный отказ, надо знать,
    # ВАЖЕН ли этот отказ, — а это суждение, и оно не уровня локальной модели.
    # Такие находки идут владельцу, а не в автоматическую правку.
    ("глухое проглатывание отказа|НЕ_АВТО",
     r"except[^\n:]*:\s*\n\s*(pass|return \[\]|return \{\}|return None)\s*$",
     "Отказ, поданный как пустой результат, неотличим от «там ничего нет». "
     "Так мы однажды доложили «работы нет», исчерпав лимит запросов."),
]


def scan_patterns():
    """Ищет повторения известных поломок. Каждая находка — с файлом и строкой."""
    found = []
    for p in _py_files():
        try:
            raw = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        code = "\n".join(_strip_comments(raw))
        rel = p.relative_to(ROOT).as_posix()
        for name, rx, why in PATTERNS:
            for m in re.finditer(rx, code, re.M):
                line = code.count("\n", 0, m.start()) + 1
                found.append({"образец": name, "где": f"{rel}:{line}",
                              "почему это плохо": why})
    return found


# ═══════════════════════════════════ ОЧЕРЕДИ: ЧИТАТЕЛЬ БЕЗ ПИСАТЕЛЯ
def scan_queues():
    """Темы сообщений, которые кто-то ЧИТАЕТ, но никто не пишет — и наоборот.

    Именно так у нас пропала вся цепочка сдачи работы: deliver() ждал тему
    'resolution', а писала её только служба на другом языке, и в рабочем коде
    писателя не было вовсе. Способность существовала и была недостижима.
    """
    # ДВА ПРОХОДА, И ЭТО НЕ ИЗЛИШЕСТВО.
    #
    # Первый проход собирает ИМЕНА тем — их видно по чтению вида topic='X'.
    # Второй ищет, кто эти темы ПИШЕТ, и ищет не по форме записи, а по смыслу:
    # рядом со вставкой в таблицу сообщений встречается имя темы.
    #
    # Почему нельзя одним проходом. Тема в записи часто передаётся позиционно,
    # без слова topic вовсе: c.execute("INSERT ... VALUES (?,?,?,?,?)",
    # ("craftsman", "executor", "documentation_request", ...)). Разбор по форме
    # объявил такого писателя несуществующим — и проверка обвинила код, который
    # я сам только что написал и проверил живым вызовом. Ложная тревога учит не
    # доверять проверке целиком, а это дороже пропущенной находки.
    files = [q for q in _py_files() if q.exists()]
    js = ROOT / "service" / "server.js"
    if js.exists():
        files.append(js)

    texts = {}
    for q in files:
        try:
            texts[q] = q.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

    rx_topic = re.compile(r"topic\s*=\s*['\"]([a-z_]+)['\"]")
    topics = set()
    for raw in texts.values():
        topics.update(rx_topic.findall(raw))

    readers, writers = {}, {}
    for q, raw in texts.items():
        rel = q.relative_to(ROOT).as_posix()
        lines = raw.splitlines()
        for i, line in enumerate(lines, 1):
            lo = max(0, i - 7)
            window = " ".join(lines[lo:i + 7]).lower()
            writes_here = any(k in window for k in
                              ("insert into messages", "_put(", "send(",
                               "insert into events", "broadcast("))
            for topic in topics:
                if f"'{topic}'" not in line and f'"{topic}"' not in line:
                    continue
                (writers if writes_here else readers).setdefault(
                    topic, []).append(f"{rel}:{i}")

    # Темы, которые кладёт общая функция шины, писателями считаются: там запись
    # идёт через один helper, и имя темы приходит параметром.
    for helper_topic in ("chat", "ask", "answer", "handoff", "judgment"):
        writers.setdefault(helper_topic, ["core/bus.py"])

    # ЧИТАТЬ МОЖНО И ПО АДРЕСАТУ. Очередь суждений разбирается запросом
    # WHERE recipient='ESCALATION' — тема в нём не упоминается вовсе. Считать
    # такую очередь непрочитанной значит объявить сломанным то, что работает
    # и чем пользуется дашборд.
    by_recipient = set()
    for raw in texts.values():
        for m in re.finditer(r"recipient\s*=\s*['\"]([A-Za-z_]+)['\"]", raw):
            by_recipient.add(m.group(1))
    if "ESCALATION" in by_recipient:
        readers.setdefault("judgment", []).append("читается по адресату ESCALATION")

    orphan_reads = {t: loc for t, loc in readers.items() if t not in writers}
    orphan_writes = {t: loc for t, loc in writers.items() if t not in readers}
    return {"читают, но никто не пишет": orphan_reads,
            "пишут, но никто не читает": orphan_writes}


# ═══════════════════════════════════ СОСТОЯНИЯ ГОНКИ И БЛОКИРОВКИ
def scan_concurrency():
    """Места, где параллельная работа может потерять или испортить данные."""
    notes = []

    db = (ROOT / "core" / "db.py").read_text(encoding="utf-8", errors="ignore")
    if "busy_timeout" not in db:
        notes.append({"беда": "нет ожидания блокировки базы",
                      "где": "core/db.py",
                      "чем грозит": "второй пишущий падает с database is locked"})
    if "journal_mode=WAL" not in db:
        notes.append({"беда": "журнал не в режиме WAL",
                      "где": "core/db.py",
                      "чем грозит": "читатель блокирует писателя и наоборот"})

    w = (ROOT / "agents" / "worker.py").read_text(encoding="utf-8", errors="ignore")
    if "_claim_slot" not in w:
        notes.append({"беда": "нет замка единственности воркера",
                      "где": "agents/worker.py",
                      "чем грозит": "два цикла делают одно и то же дважды"})

    # ВЛОЖЕННЫЙ ОБХОД КУРСОРА. На этом я уже получил неверные числа: внешний
    # курсор сбивается, когда по тому же соединению открывают внутренний.
    for p in _py_files():
        try:
            src = p.read_text(encoding="utf-8", errors="ignore")
            tree = ast.parse(src)
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.For):
                continue
            outer = ast.dump(node.iter)
            if "execute" not in outer:
                continue
            for inner in ast.walk(node):
                if inner is node or not isinstance(inner, ast.For):
                    continue
                if "execute" in ast.dump(inner.iter):
                    notes.append({
                        "беда": "вложенный обход по одному соединению",
                        "где": f"{p.relative_to(ROOT).as_posix()}:{node.lineno}",
                        "чем грозит": "внешний курсор сбивается, числа выходят "
                                      "неверными и выглядят правдоподобно"})
                    break
    return notes


# ═══════════════════════════════════ КРИТИЧЕСКИЙ ПУТЬ ДО ДЕНЕГ
def critical_path():
    """Путь от нуля до зачисления, по шагам, с честным состоянием каждого.

    Ни один шаг здесь не объявляется пройденным по наличию кода: только по
    строке в базе или живому ответу. Шаг без доказательства помечается как
    непройденный, даже если способность для него написана.
    """
    from core.db import connect
    from core.identity import SERVICE_URL
    import urllib.request
    import urllib.error

    c = connect()

    def q(sql):
        try:
            return (c.execute(sql).fetchone() or [0])[0]
        except Exception:
            return 0

    def http(path, ua="P0-audit/1.0"):
        try:
            r = urllib.request.urlopen(urllib.request.Request(
                SERVICE_URL + path, headers={"User-Agent": ua}), timeout=20)
            return r.status
        except urllib.error.HTTPError as e:
            return e.code
        except Exception:
            return 0

    n_found = q("SELECT COUNT(*) FROM bounties WHERE status='found'")
    n_claims = q("SELECT COUNT(*) FROM actions WHERE kind='bounty_claim' AND dry_run=0")
    n_merged = q("SELECT COUNT(*) FROM pull_requests WHERE state='merged'")
    n_pay = q("SELECT COUNT(*) FROM payments")
    n_award = q("SELECT COUNT(*) FROM evidence WHERE claim LIKE '%awarded%' "
                "OR claim LIKE '%payout to us%'")
    work_dir = ROOT / "work"
    n_work = len(list(work_dir.glob("*.md"))) if work_dir.exists() else 0
    tier = http("/search?q=test")

    steps = [
        ("1. найдена работа с выплатой", n_found > 0,
         f"доступных задач: {n_found}"),
        ("2. работа ПРОИЗВЕДЕНА и сверена", n_work > 0,
         f"готовых работ на диске: {n_work}"),
        ("3. заявка подана публично", n_claims > 0,
         f"настоящих заявок, не вхолостую: {n_claims}"),
        ("4. работа принята другой стороной", n_merged > 0,
         f"слитых работ: {n_merged}"),
        ("5. наша служба принимает оплату", tier == 402,
         f"платный тариф отвечает {tier}, нужен 402"),
        ("6. выплата объявлена нам", n_award > 0,
         f"объявлений о выплате в нашу пользу: {n_award}"),
        ("7. деньги ЗАЧИСЛЕНЫ на кошелёк", n_pay > 0,
         f"платежей в книге: {n_pay}"),
    ]
    c.close()

    out = []
    stopped_at = None
    for name, ok, detail in steps:
        out.append({"шаг": name, "пройден": bool(ok), "чем подтверждён": detail})
        if not ok and stopped_at is None:
            stopped_at = name
    return {"шаги": out, "обрывается на": stopped_at}


if __name__ == "__main__":
    import json
    print("=" * 76)
    print("ГЛУБОКИЕ ПРОВЕРКИ — повторения известных поломок")
    print("=" * 76)

    pats = scan_patterns()
    print(f"\n── Повторения образцов дефектов: {len(pats)}")
    for f in pats[:12]:
        print(f"  {f['где']:<34} {f['образец']}")

    qs = scan_queues()
    print("\n── Очереди без второй стороны")
    for side, items in qs.items():
        print(f"  {side}: {list(items) or 'нет'}")

    cc = scan_concurrency()
    print(f"\n── Состояния гонки и блокировки: {len(cc)}")
    for n in cc[:8]:
        print(f"  {n['где']:<34} {n['беда']}")

    cp = critical_path()
    print("\n── Критический путь до денег")
    for s in cp["шаги"]:
        print(f"  [{'ДА ' if s['пройден'] else 'НЕТ'}] {s['шаг']:<38} {s['чем подтверждён']}")
    print(f"\n  ОБРЫВАЕТСЯ НА: {cp['обрывается на'] or 'путь пройден целиком'}")
    print("=" * 76)
