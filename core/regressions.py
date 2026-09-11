"""РАСТУЩИЙ КАТАЛОГ ИНВАРИАНТОВ — аудит, который не остаётся прежним.

Владелец указал на то, чего я не видел: «6 из 6» и «91 из 91» — это числа,
которые доказывают одно и то же вечно. Проверка, не растущая от найденных
ошибок, сама становится ритуалом: она проходит, потому что её написали под
то, что уже починено. Настоящий контроль растёт быстрее системы.

Здесь каждый ПОЧИНЕННЫЙ дефект превращается в постоянный инвариант, и число
проверок увеличивается само. Инвариант — это ДАННЫЕ, а не код: система
дописывает строку в таблицу, а не сочиняет себе новый исходник. Поэтому
рост безопасен и не требует чужого доверия.

ЧТО МОЖЕТ БЫТЬ ИНВАРИАНТОМ (пять видов, каждый проверяется механически):

    absent      в указанных файлах НЕ ДОЛЖНО встречаться выражение
                («+00:00» дописывается к наивной метке — так уехали данные)
    present     в указанных файлах ОБЯЗАНО встречаться выражение
                (busy_timeout в соединении с базой)
    sql         запрос к базе обязан удовлетворять условию
                (таблица трат пуста, иначе заявление «с нуля» недействительно)
    callable    модуль:имя обязано импортироваться и быть вызываемым
                (так исчез mechanic.CYCLE и шаг падал при каждом вызове)
    http        адрес обязан отвечать ожидаемым кодом
                (платный тариф обязан отвечать 402, а не отдавать данные даром)

У каждого инварианта записано ПРОИСХОЖДЕНИЕ — та поломка, из-за которой он
появился. Инвариант без происхождения не добавляется: проверка, не выведенная
из настоящей ошибки, это догадка о будущем, а их у нас и так хватает.
"""
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.db import connect, ensure_schema, write  # noqa: E402

SCHEMA = """
CREATE TABLE IF NOT EXISTS invariants (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  kind TEXT NOT NULL,           -- absent | present | sql | callable | http
  target TEXT NOT NULL,         -- файлы, запрос, модуль:имя или адрес
  expr TEXT,                    -- выражение, условие или ожидаемый код
  origin TEXT NOT NULL,         -- КАКАЯ поломка это породила
  added_at TEXT NOT NULL,
  last_ok TEXT,
  last_fail TEXT,
  fails INTEGER NOT NULL DEFAULT 0
);
"""


def now():
    return datetime.now(timezone.utc).isoformat()


def _con():
    c = connect()
    ensure_schema(c, SCHEMA)
    return c


def add(name, kind, target, expr, origin):
    """Записывает новый инвариант. Без происхождения не принимается."""
    if kind not in ("absent", "present", "sql", "callable", "http"):
        raise ValueError(f"неизвестный вид инварианта: {kind}")
    if not origin or len(origin.strip()) < 20:
        raise ValueError("инвариант без описания поломки не принимается: "
                         "проверка, не выведенная из настоящей ошибки, — догадка")
    c = _con()
    write(c, """INSERT INTO invariants(name,kind,target,expr,origin,added_at)
                VALUES (?,?,?,?,?,?) ON CONFLICT(name) DO NOTHING""",
          (name, kind, target, expr, origin.strip(), now()))
    added = c.total_changes > 0
    c.commit(); c.close()
    return added


def _files(target):
    out = []
    for pat in target.split(","):
        pat = pat.strip()
        if not pat:
            continue
        if any(ch in pat for ch in "*?["):
            out += sorted(ROOT.glob(pat))
        else:
            p = ROOT / pat
            if p.exists():
                out.append(p)
    return out


def _check_one(inv):
    """Возвращает (прошло, подробность). Никаких исключений наружу."""
    kind, target, expr = inv["kind"], inv["target"], inv["expr"]
    try:
        if kind in ("absent", "present"):
            files = _files(target)
            if not files:
                return False, f"файлы не найдены: {target}"
            # Целиком, а не построчно. Построчный просмотр не мог совпасть ни
            # с одним многострочным образцом: инвариант с переносом строки либо
            # вечно падал, либо — и это хуже — вечно ПРОХОДИЛ как «не
            # встречается». Проверка, не способная сработать, опаснее
            # отсутствующей: она создаёт видимость охвата.
            rx = re.compile(expr, re.MULTILINE)
            hits = []
            for f in files:
                text = f.read_text(encoding="utf-8")
                for m in rx.finditer(text):
                    hits.append(f"{f.name}:{text.count(chr(10), 0, m.start()) + 1}")
            if kind == "absent":
                return (not hits), ("не встречается" if not hits
                                    else f"встречается в {hits[:3]}")
            return bool(hits), (f"есть в {hits[0]}" if hits else "ОТСУТСТВУЕТ")

        if kind == "sql":
            c = connect()
            try:
                row = c.execute(target).fetchone()
            finally:
                c.close()
            val = row[0] if row else None
            op, _, want = expr.partition(" ")
            want = float(want)
            got = float(val or 0)
            ok = {"==": got == want, "!=": got != want, ">=": got >= want,
                  "<=": got <= want, ">": got > want, "<": got < want}.get(op)
            if ok is None:
                return False, f"неизвестное условие: {expr}"
            return ok, f"получено {got:g}, ожидалось {expr}"

        if kind == "callable":
            mod, _, attr = target.partition(":")
            m = __import__(mod, fromlist=[attr])
            obj = getattr(m, attr, None)
            if obj is None:
                return False, f"в {mod} нет {attr}"
            return True, f"{target} на месте"

        if kind == "http":
            req = urllib.request.Request(
                target, headers={"User-Agent": "Mozilla/5.0 (compatible; P0/1.0)"})
            try:
                st = urllib.request.urlopen(req, timeout=20).status
            except urllib.error.HTTPError as e:
                st = e.code
            return st == int(expr), f"HTTP {st}, ожидался {expr}"
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:90]}"
    return False, "не проверено"


def run(verbose=True):
    """Гоняет ВЕСЬ каталог. Число проверок растёт само по мере находок."""
    c = _con()
    rows = [dict(r) for r in c.execute("SELECT * FROM invariants ORDER BY id")]
    c.close()
    ok = bad = 0
    fails = []
    for inv in rows:
        good, detail = _check_one(inv)
        c = _con()
        if good:
            ok += 1
            c.execute("UPDATE invariants SET last_ok=? WHERE id=?", (now(), inv["id"]))
        else:
            bad += 1
            fails.append((inv["name"], detail, inv["origin"]))
            # Нарушенный инвариант — это срочное: проверка выведена из поломки,
            # которая уже случалась, значит она случилась снова.
            try:
                from core import events
                events.publish("invariant_broken",
                               {"name": inv["name"], "detail": detail},
                               source="regressions")
            except Exception:
                pass
            c.execute("UPDATE invariants SET last_fail=?, fails=fails+1 WHERE id=?",
                      (now(), inv["id"]))
        c.commit(); c.close()
        if verbose:
            print(f"   {'ok  ' if good else 'FAIL'} {inv['name']} — {detail}")
    return ok, bad, fails


def count():
    c = _con()
    n = c.execute("SELECT COUNT(*) FROM invariants").fetchone()[0]
    c.close()
    return n


# ═══════════════════════════════════════════ ЗАСЕВ ИЗ РЕАЛЬНЫХ ПОЛОМОК
# Каждая строка ниже — не гипотеза, а ошибка, которая уже случилась и стоила
# работы. Каталог начинается с них и дальше растёт сам.
SEED = [
    ("метка времени не объявляется всемирной", "absent", "agents/*.py,core/*.py",
     r'\+\s*["\']\+00:00["\']',
     "Прежний _iso дописывал '+00:00' к наивной метке, объявляя местное время "
     "всемирным. Десять записей журнала уехали на три часа в будущее, и проверка "
     "живости показала будущее вместо прошлого. Данные при этом выглядели исправными."),

    ("соединение ждёт снятия блокировки", "present", "core/db.py",
     r"busy_timeout",
     "Без ожидания блокировки параллельный писатель получал 'database is locked' "
     "и падал. Воркер умирал целиком, потому что падение случалось внутри "
     "обработчика ошибок."),

    ("схема не применяется через executescript", "absent", "core/db.py",
     r"^\s*con\.executescript\(",
     "executescript в Python начинает с неявного COMMIT и НЕ уважает busy_timeout: "
     "падает сразу при занятой базе, сколько ни увеличивай ожидание. Это был корень "
     "остановок всей экосистемы."),

    ("дедупликация реплик без окна по времени", "absent",
     "core/bus.py,agents/worker.py,agents/growth.py,agents/postman.py",
     r"datetime\('now','-6 hours'\)\s+LIMIT 1",
     "Окно в шесть часов разрешало повтор реплики, а уборка повторов её удаляла. "
     "Две части системы работали друг против друга, в чате копились дубли."),

    ("точка входа механика на месте", "callable", "agents.mechanic:CYCLE", None,
     "Удаление соседней функции снесло CYCLE в конце файла. Шаг механика падал "
     "с AttributeError при каждом вызове, и это попало в коммит."),

    ("точка входа мастерового на месте", "callable", "agents.craftsman:CYCLE", None,
     "Тот же класс: модуль есть, атрибута нет, вызов падает молча для наблюдателя."),

    ("разведчик заработка вызываем", "callable", "agents.prospector:CYCLE", None,
     "Агент, которого никто не вызывает, работой не является."),

    ("таблица трат пуста", "sql", "SELECT COUNT(*) FROM spend", "== 0",
     "Заявление «заработано с нуля» недействительно при любой записи о тратах. "
     "Это единственное условие, отменяющее всю миссию."),

    ("платежи только с настоящим хешем", "sql",
     "SELECT COUNT(*) FROM payments WHERE tx_hash IS NULL OR length(tx_hash) < 20",
     "== 0",
     "Платёж без хеша транзакции невозможно проверить независимо. Такая строка "
     "означала бы подделку главного доказательства миссии."),

    ("утверждения не висят без источника", "sql",
     "SELECT COUNT(*) FROM evidence e LEFT JOIN sources s ON s.id=e.source_id "
     "WHERE s.id IS NULL", "== 0",
     "Утверждение без источника нельзя перепроверить. Такие строки появлялись "
     "при сбое записи источника и тихо портили доказательную базу."),

    ("платный тариф требует оплаты", "http",
     "https://x402-bazaar-rank.x402-bazaar-rank-worker.workers.dev/search?q=test", "402",
     "Полный датасет за $1.25 однажды пролежал в открытом доступе бесплатно, пока "
     "владелец не заметил сам. Ни один агент не проверял, что платное закрыто."),

    ("сервис на постоянном адресе жив", "http",
     "https://x402-bazaar-rank.x402-bazaar-rank-worker.workers.dev/health", "200",
     "Листинг на временный адрес бессмыслен: если адрес умрёт, теряется и запись "
     "в реестре, и всякая возможность получить платёж."),
]


def seed():
    """Заводит каталог из уже случившихся поломок."""
    n = 0
    for name, kind, target, expr, origin in SEED:
        if add(name, kind, target, expr, origin):
            n += 1
    return n


if __name__ == "__main__":
    new = seed()
    print("=" * 74)
    print(f"КАТАЛОГ ИНВАРИАНТОВ — {count()} проверок"
          + (f", из них новых {new}" if new else ""))
    print("=" * 74)
    ok, bad, fails = run()
    print("=" * 74)
    print(f"ИТОГ: {ok} прошло, {bad} упало")
    if fails:
        print("\nЧТО ЧИНИТЬ (и почему эта проверка вообще существует):")
        for name, detail, origin in fails:
            print(f"\n  {name}: {detail}")
            print(f"    происхождение: {origin[:150]}")
    print("=" * 74)
    sys.exit(1 if bad else 0)
