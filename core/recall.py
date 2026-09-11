"""СЕМАНТИЧЕСКАЯ ПАМЯТЬ — агент вспоминает по смыслу, а не по совпадению букв.

ЧТО БЫЛО НЕ ТАК. Вся память системы работала на точном совпадении: seen_claim()
сравнивал строки дословно, дедупликация ловила буквальные копии. Этого хватает,
чтобы не повторять одно и то же слово в слово, и совсем не хватает, чтобы
ВСПОМНИТЬ.

Разница видна на живом примере. Агент собирается написать «на задаче tscircuit
семьдесят две заявки, идти туда бессмысленно». Точное совпадение скажет: такого
не было, пиши. А по смыслу мы это уже выяснили и записали — другими словами,
про другую задачу, три часа назад. Без семантического поиска система заново
приходит к выводам, которые уже сделала, и называет это работой.

КАК УСТРОЕНО. Векторы считает локальная модель nomic-embed-text через Ollama:
768 измерений, бесплатно, ничего не уходит наружу. Векторы лежат в той же
базе — отдельное хранилище вроде Qdrant здесь было бы лишним слоем: у нас
тысячи записей, а не миллионы, и линейный поиск по ним занимает миллисекунды.
Это тот случай, когда правильный ответ — не ставить ещё одну систему.

ЧЕСТНАЯ ГРАНИЦА. Похожесть — это не истина. Две записи с близкими векторами
могут говорить противоположное. Поэтому recall() возвращает НАЙДЕННОЕ вместе
с мерой близости, а решение, считать ли это тем же самым, остаётся за тем,
кто спрашивал.
"""
import json
import math
import struct
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.db import connect, ensure_schema  # noqa: E402

OLLAMA = "http://127.0.0.1:11434/api/embed"
# ВЫБОР МОДЕЛИ СДЕЛАН ЗАМЕРОМ, А НЕ ПО НАЗВАНИЮ.
#
# Сначала стояла nomic-embed-text. Она считает векторы и выглядит рабочей —
# пока не проверишь разрешающую способность на нашем языке. Проверка:
#   «площадка требует подтверждения личности»
#     ~ «coinlaunch закрыт: требует KYC»          0.669   (тот же смысл!)
#     ~ «воркер перезапущен после падения»        0.727   (ничего общего)
# Модель поставила несвязанный текст ВЫШЕ смыслового совпадения. Это не
# слабое различение, а перевёрнутое: память на такой модели уверенно
# возвращала бы неправильное, и это хуже, чем отсутствие памяти.
#
# bge-m3 на тех же парах: 0.609 против 0.436 и 0.730 против 0.365 —
# оба случая ранжированы верно.
EMBED_MODEL = "bge-m3"
DIM = 1024

SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_vectors (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,           -- evidence | decision | lead | path | lesson
  ref_id INTEGER,               -- строка в исходной таблице, если есть
  text TEXT NOT NULL,
  vec BLOB NOT NULL,
  agent TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(kind, text)
);
"""


def now():
    return datetime.now(timezone.utc).isoformat()


def _con():
    c = connect()
    ensure_schema(c, SCHEMA)
    return c


def embed(text, timeout=90):
    """Вектор для текста. None, если модель не ответила — и это НЕ ноль.

    Вернуть нулевой вектор при отказе значило бы объявить любой текст похожим
    на любой другой. Отказ должен быть виден как отказ.
    """
    body = json.dumps({"model": EMBED_MODEL, "input": text[:2000]}).encode()
    req = urllib.request.Request(OLLAMA, data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        d = json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode())
    except (urllib.error.URLError, OSError, ValueError):
        return None
    vecs = d.get("embeddings") or ([d["embedding"]] if d.get("embedding") else [])
    if not vecs or not vecs[0]:
        return None
    return vecs[0]


def _pack(v):
    return struct.pack(f"{len(v)}f", *v)


def _unpack(b):
    return struct.unpack(f"{len(b) // 4}f", b)


def _cos(a, b):
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if not na or not nb:
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


def remember(text, kind="lesson", ref_id=None, agent=None):
    """Запоминает текст так, чтобы его можно было найти по смыслу."""
    v = embed(text)
    if v is None:
        return False
    c = _con()
    c.execute("""INSERT INTO memory_vectors(kind,ref_id,text,vec,agent,created_at)
                 VALUES (?,?,?,?,?,?) ON CONFLICT(kind,text) DO NOTHING""",
              (kind, ref_id, text[:2000], _pack(v), agent, now()))
    added = c.total_changes > 0
    c.commit(); c.close()
    return added


def recall(query, kind=None, top=5, min_sim=0.50):
    """Что мы уже знаем по смыслу этого вопроса.

    Возвращает список с мерой близости. Похожесть — не истина: две близкие
    записи могут утверждать противоположное, поэтому решение остаётся за
    тем, кто спрашивал.
    """
    qv = embed(query)
    if qv is None:
        return []
    c = _con()
    sql = "SELECT id,kind,text,agent,created_at,vec FROM memory_vectors"
    args = ()
    if kind:
        sql += " WHERE kind=?"
        args = (kind,)
    rows = c.execute(sql, args).fetchall()
    c.close()
    out = []
    for r in rows:
        sim = _cos(qv, _unpack(r[5]))
        if sim >= min_sim:
            out.append({"id": r[0], "kind": r[1], "text": r[2], "agent": r[3],
                        "at": r[4], "similarity": round(sim, 3)})
    out.sort(key=lambda x: -x["similarity"])
    return out[:top]


def already_known(claim, threshold=0.70):
    """Приходили ли мы к этому выводу раньше, пусть и другими словами.

    Именно этого не умела прежняя память: seen_claim() ловил дословный повтор
    и пропускал тот же вывод, сформулированный иначе. Система заново приходила
    к собственным заключениям и засчитывала это себе как работу.
    """
    hits = recall(claim, top=3, min_sim=threshold)
    return hits[0] if hits else None


def index_existing(limit=400):
    """Заводит семантическую память по тому, что уже накоплено.

    Без этого шага память начинается с нуля и первые дни ничего не помнит —
    при том, что в базе лежат сотни выводов, сделанных раньше.
    """
    c = _con()
    todo = c.execute("""SELECT e.id, e.claim, e.agent FROM evidence e
                        LEFT JOIN memory_vectors m
                          ON m.kind='evidence' AND m.ref_id = e.id
                        WHERE m.id IS NULL ORDER BY e.id DESC LIMIT ?""",
                     (limit,)).fetchall()
    c.close()
    n = 0
    for eid, claim, agent in todo:
        if remember(claim, kind="evidence", ref_id=eid, agent=agent):
            n += 1
    return n


def stats():
    c = _con()
    total = c.execute("SELECT COUNT(*) FROM memory_vectors").fetchone()[0]
    by = dict(c.execute("SELECT kind, COUNT(*) FROM memory_vectors GROUP BY kind"))
    c.close()
    return {"всего": total, "по видам": by}


if __name__ == "__main__":
    print("индексирую накопленное...")
    n = index_existing(200)
    print(f"добавлено векторов: {n}")
    print("состояние памяти:", stats())
    print()
    for q in ["на задаче слишком много заявок, идти туда бессмысленно",
              "площадка требует подтверждения личности",
              "выплата не дойдёт до кошелька"]:
        print(f"\nВОПРОС: {q}")
        for h in recall(q, top=3):
            print(f"   {h['similarity']}  [{h['agent']}] {h['text'][:110]}")
