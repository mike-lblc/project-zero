"""ВЫНОС КАТАЛОГА ИНВАРИАНТОВ В РЕПОЗИТОРИЙ — чтобы уроки пережили машину.

Обнаружено облачным прогоном: дома каталог насчитывает 54 проверки, в облаке —
12. Разница не в коде, а в месте хранения: заложенные в код проверки едут
вместе с ним, а всё, что система выучила потом, лежит в локальной базе. База в
репозиторий не кладётся (она большая и меняется каждую минуту), и получается,
что сорок два урока, купленных настоящими поломками, живут в единственном
экземпляре на одном диске.

Это ровно тот случай, когда «у нас есть растущий каталог» и «каталог переживёт
перезагрузку» — разные утверждения. Первое было верным, второе нет.

Здесь каталог выгружается в data/invariants.json, который КЛАДЁТСЯ в
репозиторий: файл маленький, меняется редко, и каждая строка в нём — описание
поломки, которая уже случилась. При запуске в чистой среде каталог
подхватывается обратно, и облако проверяет то же, что дом.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.regressions import _con, add, count  # noqa: E402

STORE = ROOT / "data" / "invariants.json"


def export():
    """Выгружает каталог в файл. Порядок устойчивый — чтобы различия читались."""
    c = _con()
    rows = [dict(r) for r in c.execute(
        "SELECT name, kind, target, expr, origin FROM invariants ORDER BY name")]
    c.close()
    STORE.parent.mkdir(exist_ok=True)
    STORE.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    return len(rows)


def load():
    """Подхватывает каталог из файла. Существующие не трогает.

    Возвращает число ДОБАВЛЕННЫХ. Ноль означает «всё уже было», а не «файл
    пуст»: различать это важно, иначе в чистой среде молчание выглядит успехом.
    """
    if not STORE.exists():
        return 0, "файла каталога нет"
    try:
        rows = json.loads(STORE.read_text(encoding="utf-8"))
    except ValueError as e:
        return 0, f"файл каталога не разобрался: {e}"
    added = 0
    for r in rows:
        try:
            if add(r["name"], r["kind"], r["target"], r["expr"], r["origin"]):
                added += 1
        except ValueError:
            continue          # происхождение короче порога — пропускаем, не роняя
    return added, f"в файле {len(rows)}, добавлено {added}, всего {count()}"


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "export"
    if mode == "load":
        n, detail = load()
        print(f"подхвачено из репозитория: {detail}")
    else:
        n = export()
        print(f"выгружено инвариантов: {n} -> {STORE.relative_to(ROOT)}")
