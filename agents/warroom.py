"""ШТАБ ПЕРВОГО КЛИЕНТА — раздел 13 директивы.

Директива требует: пока нет повторяемой выручки, держать пайплайн первого клиента
в наивысшем приоритете, и «внутренняя мысль обязана переходить во внешнее
экономическое действие».

Здесь два инструмента:
  score()        — скоринг возможностей по формуле раздела 10
  sell_gate()    — 10 вопросов раздела 11 «SELL BEFORE BUILD»

Оценки ставятся честно, включая наш собственный продукт. Если он проигрывает —
так и пишем, иначе скоринг превращается в самооправдание.
"""
import sys, json
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.db import connect
from core import guard

ROOT = Path(__file__).resolve().parent.parent


def now():
    return datetime.now(timezone.utc).isoformat()


SCHEMA = """
CREATE TABLE IF NOT EXISTS opportunities (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  -- числитель
  p_first_revenue REAL, expected_revenue REAL, speed REAL, resource_fit REAL,
  customer_access REAL, repeatability REAL, margin REAL, automation REAL, strategic REAL,
  -- знаменатель
  human_time REAL, ai_cost REAL, complexity REAL, financial_cost REAL, risk REAL,
  score REAL,
  gate_passed INTEGER,
  gate_notes TEXT,
  verdict TEXT,
  scored_at TEXT NOT NULL
);
"""

# Оценки 1..5. Выставлены по тому, что уже ИЗМЕРЕНО в этой сессии, а не по вкусу.
CANDIDATES = {
    "x402-эндпоинт (то, что построено)": dict(
        p_first_revenue=2, expected_revenue=1, speed=2, resource_fit=5,
        customer_access=1,          # ИЗМЕРЕНО: мы в нуле каналов, нас не найти
        repeatability=4, margin=5, automation=5, strategic=3,
        human_time=1, ai_cost=1, complexity=3, financial_cost=1, risk=2,
        note="Готов и работает. Но медианный сервис рынка получает единицы вызовов в месяц, "
             "а доступ к покупателю упирается в индексы, куда мы ещё не попали."),
    "MCP-сервер в реестре": dict(
        p_first_revenue=3, expected_revenue=2, speed=4, resource_fit=5,
        customer_access=4,          # реестр специально существует для обнаружения
        repeatability=4, margin=5, automation=5, strategic=4,
        human_time=1, ai_cost=1, complexity=2, financial_cost=1, risk=1,
        note="Тот же продукт, но витрина, которую агенты обходят сами. Не требует CDP."),
    "Email-рассылка с партнёрскими офферами": dict(
        p_first_revenue=2, expected_revenue=2, speed=1, resource_fit=4,
        customer_access=1,          # аудитории нет вообще
        repeatability=4, margin=4, automation=4, strategic=3,
        human_time=2, ai_cost=1, complexity=3, financial_cost=1, risk=3,
        note="Механика готова и проверена, но список берётся из трафика, которого нет. "
             "Недели до первой выручки."),
    "Продажа услуг (код, автоматизация, аналитика)": dict(
        p_first_revenue=4, expected_revenue=4, speed=4, resource_fit=5,
        customer_access=3,          # клиента можно найти адресно, без индексов
        repeatability=3, margin=4, automation=2, strategic=2,
        human_time=4,               # ГЛАВНЫЙ минус: нужно время владельца
        ai_cost=2, complexity=2, financial_cost=1, risk=2,
        note="Директива ставит service revenue первым. Конкретный клиент, конкретная боль, "
             "конкретная цена. Минус — требует времени владельца и его же переписки."),
    "Продажа датасета рынка x402": dict(
        p_first_revenue=3, expected_revenue=3, speed=3, resource_fit=5,
        customer_access=2, repeatability=2, margin=5, automation=4, strategic=3,
        human_time=2, ai_cost=1, complexity=1, financial_cost=1, risk=2,
        note="Актив уникален: полного краула с метриками нет ни у кого. "
             "Покупатель — тот, кто строит на x402; их немного, но они платят."),
}

# Раздел 11: 10 вопросов. Проходной балл — не менее 7 «да».
SELL_QUESTIONS = [
    "Кто конкретно в этом нуждается?",
    "За решение какой болезненной проблемы они платят?",
    "Можно ли до них дотянуться без платного трафика?",
    "Есть ли свидетельства готовности платить?",
    "Можем ли сначала сделать результат руками?",
    "Можно ли проверить через пилот?",
    "Можно ли продать до постройки?",
    "Может ли клиент профинансировать разработку?",
    "Каков минимально достаточный результат?",
    "Какое свидетельство оправдает дальнейшие вложения?",
]

GATE_ANSWERS = {
    "x402-эндпоинт (то, что построено)": [
        (True, "агенты, которым нужен поиск по платным сервисам"),
        (False, "боль слабая: официальный индекс бесплатен, наш плюс — только ранжирование"),
        (True, "реестры и Bazaar — бесплатные каналы"),
        (True, "рынок сделал 336 261 вызов и 43 113 плательщиков за 30 дней"),
        (True, "отчёт уже собирается вручную"),
        (True, "бесплатный топ-5 как пилот"),
        (False, "агенту нечего предпродать — он платит по факту вызова"),
        (False, "клиент не профинансирует, чек $0.01"),
        (True, "ранжированный список из 5 позиций"),
        (True, "первый сторонний платёж"),
    ],
    "Продажа услуг (код, автоматизация, аналитика)": [
        (True, "малый бизнес и разработчики, которым нужна автоматизация"),
        (True, "ручная работа, которую они делают руками каждую неделю"),
        (True, "профильные сообщества, биржи, прямые контакты владельца"),
        (True, "рынок услуг существует и платит десятилетиями"),
        (True, "первый заказ делается руками полностью"),
        (True, "маленький платный пилот"),
        (True, "услугу продают до исполнения — это норма"),
        (True, "предоплата клиента и есть финансирование"),
        (True, "один работающий скрипт или отчёт"),
        (True, "первая оплата от живого клиента"),
    ],
}


def _init():
    con = connect()
    con.executescript(SCHEMA)
    con.commit()
    return con


def score():
    """Скоринг по формуле раздела 10: произведение плюсов делённое на сумму минусов."""
    guard.check_action("research", "GREEN")
    con = _init()
    results = []
    for name, v in CANDIDATES.items():
        num = (v["p_first_revenue"] * v["expected_revenue"] * v["speed"] * v["resource_fit"]
               * v["customer_access"] * v["repeatability"] * v["margin"] * v["automation"]
               * v["strategic"])
        den = v["human_time"] + v["ai_cost"] + v["complexity"] + v["financial_cost"] + v["risk"]
        s = round(num / den, 1)
        con.execute("""INSERT OR REPLACE INTO opportunities
            (name,p_first_revenue,expected_revenue,speed,resource_fit,customer_access,
             repeatability,margin,automation,strategic,human_time,ai_cost,complexity,
             financial_cost,risk,score,gate_notes,scored_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (name, v["p_first_revenue"], v["expected_revenue"], v["speed"], v["resource_fit"],
             v["customer_access"], v["repeatability"], v["margin"], v["automation"],
             v["strategic"], v["human_time"], v["ai_cost"], v["complexity"],
             v["financial_cost"], v["risk"], s, v["note"], now()))
        results.append((s, name, v["note"]))
    con.commit(); con.close()
    results.sort(reverse=True)
    return results


def sell_gate(name):
    """10 вопросов раздела 11. Меньше 7 «да» — строить рано."""
    answers = GATE_ANSWERS.get(name)
    if not answers:
        return None
    yes = sum(1 for ok, _ in answers if ok)
    passed = yes >= 7
    con = _init()
    con.execute("UPDATE opportunities SET gate_passed=?, verdict=? WHERE name=?",
                (1 if passed else 0,
                 f"{yes}/10 да — {'ПРОШЁЛ' if passed else 'НЕ ПРОШЁЛ'}", name))
    con.commit(); con.close()
    return {"name": name, "yes": yes, "passed": passed,
            "detail": [(q, ok, why) for q, (ok, why) in zip(SELL_QUESTIONS, answers)]}


if __name__ == "__main__":
    print("=" * 78)
    print("СКОРИНГ ВОЗМОЖНОСТЕЙ (раздел 10 директивы)")
    print("=" * 78)
    for s, name, note in score():
        print(f"\n  {s:>10,.0f}  {name}")
        print(f"              {note}")
    print()
    print("=" * 78)
    print("ГЕЙТ «ПРОДАЙ ДО ПОСТРОЙКИ» (раздел 11)")
    print("=" * 78)
    for name in GATE_ANSWERS:
        r = sell_gate(name)
        print(f"\n{name}: {r['yes']}/10 — {'ПРОШЁЛ' if r['passed'] else 'НЕ ПРОШЁЛ'}")
        for q, ok, why in r["detail"]:
            if not ok:
                print(f"    НЕТ — {q}  ({why})")
