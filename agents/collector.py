"""СБОРЩИК ПЛАТЕЖЕЙ — выбирает маршрут, просит деньги, сверяет поступление.

Роли не было вовсе. Система умела найти работу и сделать её, а вопрос «куда
именно платить и пришло ли» решался одним захардкоженным адресом в одной сети.
Директива требует маршрутизатора, и вот его действующая часть: реестр маршрутов
лежит в `core/payment.py`, наблюдение — в `core/payment_watch.py`, а здесь
принимаются решения.

ТРИ ОТВЕТА, А НЕ ДВА. На вопрос «дойдут ли деньги этим путём» есть ответ
«неизвестно», и он законный. Маршрут, который мы не проверяли, нельзя ни
использовать, ни отбросить: директива требует отклонять работу ТОЛЬКО после
установленной недоступности выплаты. Отбросить непроверенное — значит сузить
рынок догадкой.

ЧЕГО СБОРЩИК НЕ ДЕЛАЕТ. Не переводит, не обменивает, не выводит. Он просит и
сверяет. Право получать и право распоряжаться — разные права, и второго у
системы нет ни в каком виде.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import guard, bus, payment, payment_watch  # noqa: E402
from core.db import connect  # noqa: E402


def routes_report():
    """Что у нас есть для приёма денег и в каком это состоянии."""
    guard.check_action("research", "GREEN")
    rs = payment.routes()
    verified = [r for r in rs if r["status"] == "verified"]
    blocked = [r for r in rs if r["status"] == "blocked"]
    unknown = [r for r in rs if r["status"] == "unverified"]
    nets = sorted({r["network"] for r in verified if r.get("network")})
    return (f"маршрутов {len(rs)}: проверено {len(verified)} "
            f"({', '.join(nets) if nets else 'без сетей'}), "
            f"не проверено {len(unknown)}, закрыто {len(blocked)}")


def verify_routes():
    """Проверяет маршруты живым запросом. Без даты проверки маршрут не рабочий."""
    guard.check_action("research", "GREEN")
    payment.seed()
    res = payment_watch.verify_routes()
    ok = [r for r in res if r["итог"] == "проверен"]
    bad = [r for r in res if r["итог"] != "проверен"]
    out = f"проверено маршрутов: {len(ok)} из {len(res)}"
    if bad:
        out += "; НЕ ОТВЕТИЛИ: " + "; ".join(f"{b['сеть']} ({b.get('почему')})" for b in bad)
    return out


def choose_route(currency=None, network=None, provider=None):
    """Лучший разрешённый маршрут для конкретной выплаты.

    «Лучший» здесь не про удобство, а про доказанность: проверенный маршрут
    побеждает непроверенный всегда, каким бы удобным тот ни казался.
    """
    ok, why = payment.reachable(currency=currency, network=network, provider=provider)
    if ok is True:
        return {"ok": True, "почему": why}
    if ok is False:
        return {"ok": False, "почему": why}
    return {"ok": None, "почему": why,
            "что делать": "проверить маршрут до того, как вкладывать труд"}


def request(opportunity, amount, currency, network=None, provider=None):
    """Создаёт запрос оплаты. ЗАПРОС — НЕ ПЛАТЁЖ, и путать нельзя.

    Директива перечисляет это прямо среди того, что нельзя считать прибылью:
    обещание, счёт, ожидающая выплата и неподтверждённая награда.
    """
    guard.check_action("payment_request", "YELLOW")
    route = choose_route(currency=currency, network=network, provider=provider)
    if route["ok"] is False:
        return {"ok": False, "почему": f"маршрут недоступен: {route['почему']}"}

    dest = None
    if network in ("base", "ethereum", "polygon", "arbitrum"):
        dest = payment.OWNER_DESTINATIONS["evm"]
    elif network == "bitcoin":
        dest = payment.OWNER_DESTINATIONS["btc"]

    instructions = json.dumps({
        "amount": amount, "currency": currency, "network": network,
        "destination": dest, "provider": provider,
        "note": "перевод на этот адрес считается полученным только после "
                "подтверждения хешем транзакции"},
        ensure_ascii=False)

    rid = payment.request_payment(opportunity, amount, currency,
                                  instructions=instructions)
    bus.broadcast("collector", f"Запрошена оплата за «{str(opportunity)[:60]}»: "
                               f"{amount} {currency}"
                               + (f" в сети {network}" if network else "")
                               + f". Это ЗАПРОС, а не платёж — счёт миссии не меняется.")
    return {"ok": True, "request_id": rid, "маршрут": route["почему"],
            "куда": dest, "предупреждение": "запрос не является поступлением"}


def collect():
    """Сверяет поступления по всем проверенным маршрутам."""
    guard.check_action("research", "GREEN")
    return payment_watch.watch()


def money_report():
    """Шесть состояний денег, каждое отдельно. Смешивать их нельзя."""
    st = payment.state_of_money()
    tot = payment.totals()
    line = ", ".join(f"{k}: {v}" for k, v in st.items())
    if tot:
        line += "; получено по валютам: " + ", ".join(
            f"{t['валюта']} net {t['net']}" for t in tot)
    else:
        line += "; получено: ничего"
    return line


CYCLE = [("verify_routes", verify_routes),
         ("collect_payments", collect),
         ("money_report", money_report)]


if __name__ == "__main__":
    print("маршруты:", routes_report())
    print("проверка:", verify_routes())
    print("сверка:  ", collect())
    print("деньги:  ", money_report())
