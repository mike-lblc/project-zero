"""ВЕДОМОСТЬ АВТОНОМНОСТИ: что агенты МОГУТ БЕЗ ЧЕЛОВЕКА, проверено запуском.

Владелец спросил четыре раза одно и то же: «если тебя выключить, агенты смогут
сделать ВСЁ, что сделал ты, чтобы получить платёж?» — и четыре раза получил прозу.
Проза здесь не годится: я уже однажды доложил «развёрнуто и проверено» на сервисе,
где сам же и сломал маршрут. Поэтому ответ должен быть МЕХАНИЧЕСКИМ и повторяемым.

Ведомость разделяет ДВА разных вопроса и путать их нельзя:

  (а) АВТОНОМНОСТЬ — экосистема делает это без человека. Обязательно:
      В ЦИКЛЕ   — шаг кто-то вызывает сам;
      В ОБЛАКЕ  — он в CLOUD_STEPS, то есть работает при выключенном ноутбуке;
      КЛЮЧИ     — его секреты переданы облаку, а не лежат только в .env;
      ХОД       — по часам (CRITICAL_EVERY) или в общей очереди редких шагов;
      ЖИВАЯ     — и, где это безопасно, шаг РЕАЛЬНО ЗАПУСКАЕТСЯ и отвечает.
  (б) АГЕНТ? — может ли агент ВЫБРАТЬ это своим решением вне расписания.
      Полезно для срочного, но САМО ПО СЕБЕ не автономность.

Провалом считается только (а). Первая версия этой ведомости смешивала признаки и
насчитала три ложных провала: `declare_routes` и `register_indexes` вызываются
ВНУТРИ `sell_surface.cycle()` — человек не нужен, а ведомость говорила «НЕТ».
Инструмент проверять всё равно стоит: `self_deploy` лежал в реестре улучшателя и
не мог быть вызван вообще, потому что класс RED выше предела агента, а постоянного
разрешения не было. Такие расхождения ведомость называет отдельным блоком.

Запуск:  py -3.13 -X utf8 ops/autonomy_ledger.py
         py -3.13 -X utf8 ops/autonomy_ledger.py --live    (+ живые вызовы)
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import guard, roster  # noqa: E402
from core.agent import REGISTRY, TOOLS  # noqa: E402
from agents import worker  # noqa: E402

RANK = {"GREEN": 0, "YELLOW": 1, "RED": 2, "BLACK": 3}

# Звенья цепочки «получить платёж». Порядок — от товара к деньгам.
#   tool        имя инструмента в реестре (или None, если звено не инструмент)
#   step        имя шага цикла, который его вызывает
#   secret      переменная окружения, без которой в облаке звено мертво
#   live        безопасно ли вызвать прямо сейчас (только чтение/идемпотентное)
LINKS = [
    {"name": "добавить платный маршрут", "tool": "declare_routes", "step": "sell_surface",
     "secret": "BOARD_TOKEN", "live": False,
     "why": "человек правил worker/src/index.js и деплоил; агент объявляет маршрут данными"},
    {"name": "поставить цену по рынку", "tool": "sell_surface", "step": "sell_surface",
     "secret": "BOARD_TOKEN", "live": False,
     "why": "человек переставлял цены руками"},
    {"name": "создать объявление в каталоге", "tool": "sell_surface", "step": "sell_surface",
     "secret": None, "live": False,
     "why": "человек создал 11 объявлений руками — именно они принесли платёж"},
    {"name": "починить расхождение цены", "tool": "sell_surface", "step": "sell_surface",
     "secret": "NOHUMANS_TOKENS", "live": False,
     "why": "каталог за price_drift ВАЛИТ маршрут; ключи правки были только в .env"},
    {"name": "зарегистрироваться в индексах", "tool": "register_indexes", "step": "sell_surface",
     "secret": None, "live": True,
     "why": "человек регистрировал в x402scan и agent402 руками"},
    {"name": "проверить ворота покупателя", "tool": "validate_surface", "step": "sell_surface",
     "secret": None, "live": True,
     "why": "проверка Coinbase: 25 обязательных проверок + симуляция платежа"},
    {"name": "развернуть платный сервис", "tool": "deploy_if_changed", "step": "deploy_if_changed",
     "secret": "CLOUDFLARE_API_TOKEN", "live": False, "needs_runtime": "node",
     "why": "ЭТО человек делал сам: npx wrangler deploy"},
    {"name": "исполнить гипотезу наружу", "tool": "strategist_think", "step": "strategist_think",
     "secret": "MOLTBOOK_API_KEY", "live": False,
     "why": "превращает план во внешнее действие; без него агенты только предлагают"},
    {"name": "следить за объявлениями", "tool": "directory_watch", "step": "directory_watch",
     "secret": None, "live": True,
     "why": "единственный канал, который нам платит"},
    {"name": "заметить платёж", "tool": None, "step": "watch_payments",
     "secret": None, "live": True,
     "why": "это уже работало без человека — sBTC заметил облачный цикл сам"},
    {"name": "счёт разных покупателей", "tool": "buyers", "step": "sell_surface",
     "secret": None, "live": True,
     "why": "цель владельца — 5 разных плательщиков"},
    {"name": "искать работу за деньги", "tool": None, "step": "hunt_bounties",
     "secret": None, "live": False,
     "why": "второй покупатель (sBTC) пришёл именно из работы по награде"},
    {"name": "сдать готовую работу", "tool": None, "step": "fulfil",
     "secret": "GH_TOKEN", "live": False,
     "why": "сдача в ЧУЖОЙ репозиторий: токен облака умеет только наш"},
]

CLOUD_ENV = {
    # Что реально передано облаку — читается из workflow, а не из моей памяти.
}


LOOP_STEP = "- name: Непрерывный облачный цикл"


def _loop_step_at(wf):
    """Где в файле НАЧИНАЕТСЯ шаг непрерывного цикла.

    Искать голую фразу нельзя: она встречается и в шапке файла, и в комментариях.
    Однажды это уже дало ложные провалы — ведомость прочитала блок из шапки и
    доложила, что секреты облаку не переданы, хотя они переданы. Ведомость,
    которая врёт, хуже отсутствующей, поэтому привязываемся к объявлению шага.
    """
    return wf.find(LOOP_STEP)


def _cloud_env_names():
    """Какие переменные окружения облако реально передаёт шагу цикла."""
    try:
        import re
        wf = (ROOT / ".github" / "workflows" / "agents.yml").read_text(encoding="utf-8")
        at = _loop_step_at(wf)
        if at < 0:
            return set()
        block = wf[at:]
        block = block[:block.find("run: |")]
        return set(re.findall(r"^\s{10}([A-Z0-9_]+):", block, re.M))
    except Exception:
        return set()


def _runtime_ready_in_cloud(need):
    """Есть ли в облаке БИНАРНИК, без которого звено мертво, и ДО шага цикла.

    Урок, купленный дорого. `deploy_if_changed` был в облаке, с секретом и по
    часам — то есть по всем признакам «автономен». А развернуть не мог ни разу:
    `actions/setup-node` стоял ПОСЛЕ непрерывного цикла, а worker/node_modules
    в репозиторий не коммитится. Собственные ворота деплоя (`node --check` и
    тесты воркера) падали на отсутствующем node.

    Поэтому ведомость смотрит не только на проводку, но и на ПОРЯДОК ШАГОВ: нужный
    инструмент обязан ставиться раньше того шага, который им пользуется.
    """
    try:
        wf = (ROOT / ".github" / "workflows" / "agents.yml").read_text(encoding="utf-8")
    except Exception:
        return False, "workflow не прочитан"
    loop_at = _loop_step_at(wf)
    if loop_at < 0:
        return False, "шаг непрерывного цикла не найден"
    before = wf[:loop_at]
    if need == "node":
        if "setup-node" not in before:
            return False, "setup-node стоит ПОСЛЕ цикла — node в цикле недоступен"
        if "--prefix worker" not in before:
            return False, "зависимости воркера не ставятся до цикла (нет wrangler)"
        return True, "node и зависимости воркера ставятся до цикла"
    return True, "особых бинарников не нужно"


def _dispatchable(tool_name):
    """Может ли ХОТЬ ОДИН агент выбрать этот инструмент своим решением.

    Именно здесь пряталось расхождение: инструмент есть в реестре агента, но его
    класс выше предела агента и постоянного разрешения нет — диспетчер молча
    отказывает, и автономность существует только на бумаге.
    """
    if tool_name not in TOOLS:
        return False, "инструмента нет в реестре"
    t = TOOLS[tool_name]
    standing = bool(guard.standing(tool_name))
    for a in REGISTRY.values():
        if tool_name not in a.tools:
            continue
        if t.action_class == "BLACK":
            continue
        if RANK.get(t.action_class, 9) > RANK.get(a.max_class, -1) and not standing:
            continue
        return True, f"{a.name} может вызвать ({t.action_class} ≤ {a.max_class}"  \
                     f"{', есть разрешение' if standing else ''})"
    owners = [a.name for a in REGISTRY.values() if tool_name in a.tools]
    if owners:
        return False, (f"есть у {', '.join(owners)}, но класс {t.action_class} выше их предела "
                       f"и постоянного разрешения нет")
    return False, "не выдан ни одному агенту"


def audit(live=False):
    roster.wire()
    cloud_steps = set(worker.CLOUD_STEPS)
    steps = dict(worker.CYCLE + worker.SLOW_CYCLE)
    on_clock = set(getattr(worker, "CRITICAL_EVERY", {}))
    passed_env = _cloud_env_names()
    rows = []
    for link in LINKS:
        r = {"name": link["name"], "why": link["why"], "gaps": []}

        # 1. МОЖЕТ ЛИ АГЕНТ ВЫБРАТЬ ЭТО САМ — это НЕ условие автономности.
        #
        # Первая версия ведомости считала звено неавтономным, если инструмента нет
        # в реестре агентов, и насчитала три ложных провала: `declare_routes` и
        # `register_indexes` вызываются ВНУТРИ `sell_surface.cycle()`, который стоит
        # в облаке и ходит по часам. То есть человек не нужен, а ведомость врала.
        #
        # Разделяем два разных вопроса. Обязателен первый:
        #   (а) экосистема делает это БЕЗ ЧЕЛОВЕКА — шаг в облаке, ключи есть;
        #   (б) агент может ВЫБРАТЬ это своим решением вне расписания — полезно,
        #       когда что-то нужно срочно, но само по себе не автономность.
        # Провалом считается только (а).
        if link["tool"]:
            ok, detail = _dispatchable(link["tool"])
            r["choose"] = "да" if ok else "нет"
            r["choose_detail"] = detail
        else:
            r["choose"] = "—"
            r["choose_detail"] = "звено — шаг цикла, инструмента нет"

        # 2. кто-то вызывает его сам
        in_cycle = link["step"] in steps
        r["cycle"] = "да" if in_cycle else "НЕТ"
        if not in_cycle:
            r["gaps"].append(f"шаг {link['step']} не стоит ни в одном цикле")

        # 3. работает при выключенном ноутбуке
        in_cloud = link["step"] in cloud_steps
        r["cloud"] = "да" if in_cloud else "НЕТ"
        if not in_cloud:
            r["gaps"].append(f"шаг {link['step']} не работает в облаке (CLOUD_STEPS)")
        r["clock"] = "по часам" if link["step"] in on_clock else "в очереди"

        # 4. ключи доступны облаку
        if link["secret"]:
            has = link["secret"] in passed_env
            r["secret"] = f"{'да' if has else 'НЕТ'} ({link['secret']})"
            if not has:
                r["gaps"].append(f"секрет {link['secret']} не передан облаку")
        else:
            r["secret"] = "не нужен"

        # 4б. БИНАРНИК В ОБЛАКЕ И ПОРЯДОК ШАГОВ.
        need = link.get("needs_runtime")
        if need:
            ok, detail = _runtime_ready_in_cloud(need)
            r["runtime"] = ("да" if ok else "НЕТ") + f" ({need})"
            if not ok:
                r["gaps"].append(f"в облаке нет {need}: {detail}")
        else:
            r["runtime"] = "—"

        # 5. живой вызов, где это безопасно
        r["live"] = "—"
        if live and link["live"] and link["tool"] in TOOLS:
            try:
                out = TOOLS[link["tool"]].fn()
                r["live"] = "ответил"
                r["live_detail"] = str(out)[:110]
            except Exception as e:
                r["live"] = "УПАЛ"
                r["live_detail"] = f"{type(e).__name__}: {str(e)[:90]}"
                r["gaps"].append(f"живой вызов упал: {type(e).__name__}")

        r["autonomous"] = not r["gaps"]
        rows.append(r)
    return rows


def report(live=False):
    rows = audit(live=live)
    ok = [r for r in rows if r["autonomous"]]
    bad = [r for r in rows if not r["autonomous"]]
    print("=" * 78)
    print("ВЕДОМОСТЬ АВТОНОМНОСТИ ДЕНЕЖНОЙ ЦЕПОЧКИ")
    print("=" * 78)
    print(f"{'звено':<30} {'цикл':<5} {'обл':<4} {'ключи':<24} {'ход':<10} {'node':<9} {'агент?'}")
    print("-" * 78)
    for r in rows:
        mark = "OK " if r["autonomous"] else "НЕТ"
        print(f"{mark} {r['name'][:26]:<26} {r['cycle']:<5} "
              f"{r['cloud']:<4} {r['secret'][:22]:<24} {r['clock']:<10} "
              f"{r.get('runtime','—'):<9} {r['choose']}")
    print("-" * 78)
    print(f"АВТОНОМНО: {len(ok)} из {len(rows)}")
    if bad:
        print()
        print("ЧТО ЕЩЁ ТРЕБУЕТ ЧЕЛОВЕКА (и почему это важно):")
        for r in bad:
            print(f"  * {r['name']}")
            print(f"      зачем: {r['why']}")
            for g in r["gaps"]:
                print(f"      НЕ ХВАТАЕТ: {g}")
    cant_choose = [r for r in rows if r["choose"] == "нет"]
    if cant_choose:
        print()
        print("РАБОТАЕТ САМО, НО АГЕНТ НЕ МОЖЕТ ВЫБРАТЬ ЭТО ВНЕ РАСПИСАНИЯ:")
        print("  (не провал автономности — но срочное придётся ждать своего часа)")
        for r in cant_choose:
            print(f"  * {r['name']}: {r['choose_detail']}")
    if live:
        print()
        print("ЖИВЫЕ ВЫЗОВЫ:")
        for r in rows:
            if r["live"] != "—":
                print(f"  {r['live']:<8} {r['name'][:30]:<30} {r.get('live_detail','')[:90]}")
    return rows


if __name__ == "__main__":
    report(live="--live" in sys.argv)
