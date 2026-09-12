"""ПРИОРИТЕТ ВОЗМОЖНОСТИ — формула GND §9, одна на всю систему.

    expected_net_value × probability_of_acceptance × payout_reachability
    ÷ time_to_cash ÷ execution_cost ÷ competition

ЧЕГО ЗДЕСЬ НЕТ, И ЭТО НАМЕРЕННО. Функция не принимает ни валюту, ни сеть, ни
провайдера. Директива запрещает поднимать работу за то, что она платит в
крипте, и за то, что адаптер Base уже написан. Запрет, записанный словами,
обходят одной правкой; запрет, записанный сигнатурой, обойти нельзя: сети в
расчёт просто не передать. Способ оплаты влияет только через достижимость
выплаты — «дойдут ли деньги», три ответа маршрутизатора, — а не через своё имя.

ОЦЕНКИ ОСТАЮТСЯ ОЦЕНКАМИ. Срок до денег и стоимость исполнения мы не измеряем
заранее — мы их оцениваем по размеру и виду работы. Поэтому каждая оценка
возвращается вместе с формулой и называется оценкой, а не фактом.

При равенстве директива называет порядок: объявленный бюджет, быстрая
приёмка, доступная выплата, существующие инструменты, повторяемость, продукт.
Он реализован ключом сортировки tie_key().
"""
from dataclasses import dataclass

# «Дойдут ли деньги» — три ответа маршрутизатора, а не два. Неизвестный маршрут
# не запрещён и не равен проверенному.
REACHABILITY = {True: 1.0, None: 0.35, False: 0.0}


@dataclass
class Estimate:
    expected_net_value: float        # сумма за вычетом известных комиссий, USD
    probability_of_acceptance: float  # 0..1: примут ли работу и заплатят ли нам
    payout_reachability: float       # 0..1: из REACHABILITY
    time_to_cash_days: float         # оценка: дней от начала до денег
    execution_cost: float            # оценка: часов работы
    competition: float               # 0 — никого; растёт с числом соискателей
    # признаки для разбора равенства — порядок из директивы
    budget_declared: bool = False
    quick_acceptance: bool = False
    existing_tools: bool = False
    repeatable: bool = False
    product: bool = False


def score(e: Estimate) -> float:
    """Сама формула. Делители ограничены снизу: ноль часов не значит бесконечность."""
    return round(e.expected_net_value
                 * max(0.0, min(1.0, e.probability_of_acceptance))
                 * max(0.0, min(1.0, e.payout_reachability))
                 / max(0.5, e.time_to_cash_days)
                 / max(0.25, e.execution_cost)
                 / (1.0 + max(0.0, e.competition)), 4)


def tie_key(e: Estimate):
    """Ключ сортировки: сначала формула, при равенстве — порядок директивы."""
    return (-score(e), not e.budget_declared, not e.quick_acceptance,
            e.payout_reachability < 1.0, not e.existing_tools,
            not e.repeatable, not e.product)


def explain(e: Estimate) -> str:
    return (f"{e.expected_net_value:g}$ × p{e.probability_of_acceptance:.2f} × "
            f"выплата {e.payout_reachability:.2f} ÷ {e.time_to_cash_days:g} дн ÷ "
            f"{e.execution_cost:g} ч ÷ (1+{e.competition:.2f}) = {score(e):g}")


def bounty_estimate(amount, reachable, declared, trust, stack_fit, rivals, comments,
                    is_contest=False, offsite=False, participants=None, prizes=None):
    """Оценка задачи с наградой. Чем оценено каждое слагаемое — названо рядом.

    Срок и труд растут с суммой: крупная премия почти всегда крупная работа.
    У конкурса платят одному победителю, поэтому вероятность — доля, а не
    уверенность, и ждать итога приходится до конца конкурса.
    """
    if is_contest:
        # КОНКУРС СЧИТАЕТСЯ ПО МЕСТАМ И УЧАСТНИКАМ. Прежние «два процента» для
        # хакатона с 25 510 регистрациями давали ожидание $14 800 от фонда
        # $740 000 — и конкурс вставал выше любой настоящей задачи. Ожидаемый
        # приз — фонд, делённый на число мест; шанс — места на участников.
        # Участники неизвестны — шанс берётся малым, а не средним.
        places = max(1, int(prizes or 1))
        amount = float(amount) / places
        p = (min(0.05, places / max(50, int(participants))) if participants
             else 0.002)
        ttc, cost = 30.0, 24.0
    else:
        declared_p = 1.0 if declared is True else (0.5 if declared is None else 0.08)
        p = declared_p * (0.35 + 0.65 * trust) * stack_fit
        ttc = 3.0 if amount <= 50 else (7.0 if amount <= 200 else 14.0)
        cost = 2.0 if amount <= 50 else (5.0 if amount <= 200 else 12.0)
    return Estimate(
        expected_net_value=float(amount),
        probability_of_acceptance=p,
        payout_reachability=REACHABILITY.get(reachable, 0.35),
        time_to_cash_days=ttc,
        execution_cost=cost,
        competition=rivals * 0.8 + (comments or 0) * 0.02,
        budget_declared=declared is True,
        quick_acceptance=not is_contest and amount <= 50,
        existing_tools=stack_fit >= 0.8,
        repeatable=False,
        product=False,
    )
