"""ИЗВЛЕЧЕНИЕ ДАННЫХ — исполнитель услуги «Data Extraction» из GND §5.

Зачем отдельный модуль. В плане услуга значилась исполнимой со ссылкой на
`core/sources.py`, но это наш собственный приём данных из одиннадцати
заранее известных источников, а не работа на заказ. Назвать это услугой
значило бы создать ту самую видимость возможностей, которую директива
запрещает. Здесь — настоящая работа: чужой публичный адрес на входе,
проверенный файл на выходе.

    извлечение        JSON-массив, CSV или HTML-таблицы
    очистка           пробелы, HTML-сущности, сноски вида [1]
    нормализация      имена колонок в snake_case, одинаковые у всех строк
    дедупликация      точное совпадение строки после очистки
    экспорт           CSV, JSON и XLSX одновременно
    отчёт             адрес, время, хеш исходника, строк до и после

ПРОВЕРКА КАЧЕСТВА — механическая, а не на глаз. Каждое непустое значение
результата обязано дословно встречаться в тексте исходника. Значение, которого
в исходнике нет, — это выдумка, и с ней результат не сохраняется. Так же
перечитываются записанные файлы: число строк в них должно совпасть с отчётом.

ЧЕГО ИСПОЛНИТЕЛЬ НЕ ДЕЛАЕТ. Не входит в аккаунты, не обходит защиту от
роботов и не берёт закрытые данные. Страница за входом или за капчей —
внешний блокер, а не повод притвориться браузером.
"""
import csv
import hashlib
import html as _html
import io
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import guard  # noqa: E402

OUT = ROOT / "data" / "deliveries"
UA = {"User-Agent": "P0-extractor/1.0 (+public data only)"}
MAX_BYTES = 5_000_000


def now():
    return datetime.now(timezone.utc).isoformat()


def _fetch(url, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read(MAX_BYTES + 1)
        ctype = r.headers.get("Content-Type", "")
    if len(raw) > MAX_BYTES:
        # Обрезанный исходник дал бы обрезанную таблицу, выданную за полную.
        raise ValueError(f"исходник больше {MAX_BYTES} байт — неполную выгрузку не выдаём")
    return raw, ctype


def _clean(v):
    v = _html.unescape(re.sub(r"<[^>]+>", " ", str(v)))
    v = re.sub(r"\[\d+\]|\[[a-z]\]", "", v)          # сноски википедии
    return re.sub(r"\s+", " ", v).strip()


def _scalar(v):
    """Значение JSON в том виде, в каком оно записано в исходнике.

    str(False) даёт «False», str(None) — «None», а в исходнике стоят false и
    null. Проверка качества поймала это на первом же прогоне и отказалась
    сдавать результат: значений, которых в исходнике нет, быть не должно.
    """
    if v is None:
        return ""
    if isinstance(v, str):
        return _clean(v)
    return json.dumps(v, ensure_ascii=False)


def _col(name, i):
    n = re.sub(r"[^\w]+", "_", _clean(name).lower(), flags=re.U).strip("_")
    return n or f"col_{i + 1}"


def _tables_from_html(text):
    tables = []
    for t in re.findall(r"(?is)<table[^>]*>(.*?)</table>", text):
        rows = []
        for tr in re.findall(r"(?is)<tr[^>]*>(.*?)</tr>", t):
            cells = re.findall(r"(?is)<t[hd][^>]*>(.*?)</t[hd]>", tr)
            if cells:
                rows.append([_clean(c) for c in cells])
        if len(rows) >= 2:
            tables.append(rows)
    return tables


def _rows_to_records(rows):
    head = rows[0]
    cols, seen = [], set()
    for i, h in enumerate(head):
        c = _col(h, i)
        while c in seen:
            c += "_2"
        seen.add(c)
        cols.append(c)
    out = []
    for r in rows[1:]:
        if len(r) != len(cols):
            continue                     # строка другой формы — объединённые ячейки
        out.append(dict(zip(cols, r)))
    return cols, out


def extract(url, table_index=None):
    """Сырые записи из источника. Возвращает (колонки, записи, сырьё, вид)."""
    raw, ctype = _fetch(url)
    text = raw.decode("utf-8", "ignore")
    if "json" in ctype or text.lstrip()[:1] in ("[", "{"):
        data = json.loads(text)
        if isinstance(data, dict):
            lists = [v for v in data.values() if isinstance(v, list)]
            data = max(lists, key=len) if lists else [data]
        recs = [r for r in data if isinstance(r, dict)]
        cols = []
        for r in recs:
            for k in r:
                if _col(k, 0) not in cols:
                    cols.append(_col(k, 0))
        recs = [{_col(k, 0): _scalar(v) for k, v in r.items()} for r in recs]
        return cols, recs, text, "json"
    if "csv" in ctype or url.lower().endswith(".csv"):
        rows = [[_clean(c) for c in r] for r in csv.reader(io.StringIO(text)) if r]
        cols, recs = _rows_to_records(rows)
        return cols, recs, text, "csv"
    tables = _tables_from_html(text)
    if not tables:
        return [], [], text, "html"
    if table_index is None:
        table_index = max(range(len(tables)), key=lambda i: len(tables[i]))
    cols, recs = _rows_to_records(tables[table_index])
    return cols, recs, text, "html"


def dedupe(cols, recs):
    seen, out = set(), []
    for r in recs:
        key = tuple(r.get(c, "") for c in cols)
        if key in seen:
            continue
        seen.add(key)
        out.append({c: r.get(c, "") for c in cols})
    return out


def verify(cols, recs, source_text, kind):
    """Каждое непустое значение обязано встречаться в исходнике дословно."""
    hay = _clean(source_text) if kind == "html" else source_text
    if kind == "json":
        hay = _clean(json.dumps(json.loads(source_text), ensure_ascii=False)) + " " + _clean(source_text)
    missing = []
    for i, r in enumerate(recs):
        for c in cols:
            v = r.get(c, "")
            if v and v not in hay and _clean(v) not in hay:
                missing.append(f"строка {i + 1}, {c}: «{v[:40]}»")
                if len(missing) >= 5:
                    return missing
    return missing


def produce(url, name=None, table_index=None):
    """Весь путь до файла. Сохраняет ТОЛЬКО прошедшее проверку."""
    guard.check_action("research", "GREEN")
    try:
        cols, recs, src, kind = extract(url, table_index)
    except (urllib.error.URLError, OSError, ValueError) as e:
        return {"ok": False, "why": f"источник не прочитан: {type(e).__name__}: {str(e)[:120]}"}
    if not recs:
        return {"ok": False, "why": "таблиц или записей в источнике не нашлось — "
                                    "придумывать структуру не станем"}
    clean = dedupe(cols, recs)
    bad = verify(cols, clean, src, kind)
    if bad:
        return {"ok": False, "why": "значения не найдены в исходнике: " + "; ".join(bad)}

    slug = name or re.sub(r"[^\w]+", "-", url.split("//")[-1])[:60].strip("-")
    d = OUT / slug
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "data.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(clean)
    (d / "data.json").write_text(json.dumps(clean, ensure_ascii=False, indent=1), encoding="utf-8")
    xlsx = None
    try:
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.append(cols)
        for r in clean:
            ws.append([r[c] for c in cols])
        wb.save(d / "data.xlsx")
        xlsx = "data.xlsx"
    except ImportError:
        pass
    report = {"источник": url, "вид": kind, "снято": now(),
              "sha256_исходника": hashlib.sha256(src.encode("utf-8")).hexdigest(),
              "колонки": cols, "строк_извлечено": len(recs),
              "строк_после_дедупликации": len(clean),
              "дублей_удалено": len(recs) - len(clean),
              "файлы": ["data.csv", "data.json"] + ([xlsx] if xlsx else []),
              "проверка": "каждое непустое значение найдено в исходнике дословно"}
    (d / "sources.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    # Перечитываем записанное: файл, который не совпадает с отчётом, не сдаётся.
    with open(d / "data.csv", encoding="utf-8") as f:
        n_csv = sum(1 for _ in csv.DictReader(f))
    n_json = len(json.loads((d / "data.json").read_text(encoding="utf-8")))
    if n_csv != len(clean) or n_json != len(clean):
        return {"ok": False, "why": f"записанные файлы расходятся с отчётом: csv {n_csv}, "
                                    f"json {n_json}, ожидалось {len(clean)}"}
    return {"ok": True, "папка": str(d.relative_to(ROOT)), **report}


if __name__ == "__main__":
    print(json.dumps(produce(sys.argv[1]), ensure_ascii=False, indent=1))
