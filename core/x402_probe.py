"""ПРОБА ЧУЖОЙ СЛУЖБЫ НА ТУ ЖЕ БОЛЕЗНЬ, ЧТО БЫЛА У НАС.

Мы объявляли себя версией 2 протокола оплаты и понимали только заголовок
версии 1. Современный платящий агент отправлял PAYMENT-SIGNATURE, не получал
ответа и видел 402 снова — даже с совершенно правильной подписью. Заплатить
нам было физически невозможно, а со стороны это выглядело как «никто не
покупает».

Болезнь не уникальна: спецификация сменила имена заголовков, а службы писались
по старым примерам. Здесь проба, которая отличает эту поломку от исправной
работы, — и это единственный честный повод написать чужой команде. Письмо «у
нас есть данные, купите» — спам; письмо «ваша служба не принимает оплату от
клиентов новой версии, вот как проверить» — помощь.

КАК РАЗЛИЧАЕМ. Отправляем два запроса с одинаковой по форме подписью, меняя
только имя заголовка:

    заголовок версии 2 -> «требуется оплата»   служба его НЕ ПРОЧИТАЛА
    заголовок версии 1 -> «проверка не прошла»  служба его прочитала

Если ответы различаются именно так — служба глуха к версии 2. Если оба ответа
одинаковы, вывода нет: возможно, она не читает ни один, возможно оба, и
утверждать что-либо нельзя.

ЧЕГО ЭТА ПРОБА НЕ ДЕЛАЕТ. Не платит, не подписывает ничего настоящим ключом и
не отправляет средств: подпись заведомо недействительна, и её отклонение —
ожидаемый ответ, а не сбой.
"""
import base64
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ТОЛЬКО ЛАТИНИЦА. Значение заголовка HTTP кодируется latin-1, и кириллица в
# нём роняет запрос ещё до отправки — проба возвращала «код 0» и выглядела как
# недоступность чужой службы, хотя не уходила никуда. Ровно так однажды
# ломалась и сама оплата: русский текст в base64 через btoa превращал 402 в
# ошибку сервера.
UA = {"User-Agent": "x402-compat-probe/1.0 (header compatibility check)",
      "Accept": "application/json"}


def _fake_signature():
    """Заведомо недействительная подпись правильной ФОРМЫ.

    Форма важна: служба должна дойти до проверки подписи, а не отвергнуть
    запрос как искажённый. Недействительность тоже важна — платить мы здесь
    ничего не собираемся.
    """
    return base64.b64encode(json.dumps({
        "x402Version": 2, "scheme": "exact", "network": "eip155:8453",
        "payload": {"signature": "0x" + "00" * 65,
                    "authorization": {"from": "0x" + "00" * 20,
                                      "to": "0x" + "00" * 20,
                                      "value": "10000", "validAfter": "0",
                                      "validBefore": "99999999999",
                                      "nonce": "0x" + "00" * 32}}},
        ensure_ascii=False).encode()).decode()


def _ask(url, headers, timeout=20):
    try:
        r = urllib.request.urlopen(
            urllib.request.Request(url, headers=dict(UA, **headers)), timeout=timeout)
        return r.status, r.read(600).decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, e.read(600).decode("utf-8", "ignore")
    except Exception as e:
        return 0, type(e).__name__


def _kind(code, body):
    """Что именно ответила служба: требование оплаты или отказ проверки."""
    low = (body or "").lower()
    if code != 402:
        return f"код {code}"
    if "payment required" in low or '"accepts"' in low and "failed" not in low:
        return "требует оплату"
    if "failed" in low or "verify" in low or "invalid" in low or "reason" in low:
        return "проверяет подпись"
    return "402, вид неясен"


def check(url):
    """Различает три состояния: глуха к версии 2, исправна, вывода нет."""
    sig = _fake_signature()
    none_code, none_body = _ask(url, {})
    if none_code != 402:
        return {"вывод": "не x402-эндпоинт",
                "подробности": f"без заголовка ответила {none_code}"}

    v2 = _kind(*_ask(url, {"PAYMENT-SIGNATURE": sig}))
    v1 = _kind(*_ask(url, {"X-PAYMENT": sig}))

    if v2 == "требует оплату" and v1 == "проверяет подпись":
        return {"вывод": "ГЛУХА К ВЕРСИИ 2",
                "подробности": f"заголовок версии 2 не прочитан ({v2}), "
                               f"версии 1 прочитан ({v1})",
                "чем это плохо": "клиент новой версии получает 402 даже с верной "
                                 "подписью — заплатить такой службе нельзя"}
    if v2 == "проверяет подпись":
        return {"вывод": "исправна", "подробности": "заголовок версии 2 читается"}
    return {"вывод": "вывода нет",
            "подробности": f"версия 2: {v2}; версия 1: {v1} — различия не видно"}


def check_self():
    """Проба на самих себе. Проверка, которую нельзя применить к себе, не проверка."""
    from core.identity import SERVICE_URL
    return check(SERVICE_URL + "/search?q=test")


if __name__ == "__main__":
    targets = sys.argv[1:] or None
    if not targets:
        print("на себе:", json.dumps(check_self(), ensure_ascii=False))
    else:
        for t in targets:
            print(f"{t}: {json.dumps(check(t), ensure_ascii=False)}")
