"""КАТАЛОГ УСЛУГ — только то, что система умеет довести до доказательства.

GND §5-6 перечисляет двадцать девять услуг и требует у каждой одиннадцать
обязательных полей. Владелец решил: заводить ТОЛЬКО исполнимые. Каталог, где
двадцать четыре строки из двадцати девяти невыполнимы, создаёт видимость
возможностей — ровно ту, из-за которой мы весь день принимали сломанное за
работающее.

«ИСПОЛНИМА» ЗДЕСЬ ПРОВЕРЯЕТСЯ, А НЕ ЗАЯВЛЯЕТСЯ. При заведении каталога:
  * все одиннадцать полей обязаны быть непустыми;
  * исполнитель и проверка качества — это имена функций вида модуль:функция,
    и они обязаны импортироваться и вызываться;
  * способ оплаты обязан совпасть хотя бы с одним маршрутом в
    core/payment.py, который не закрыт.
Не прошла любая проверка — услуга уходит в список «пока не умеем» с
названной причиной. Написать в каталоге «исполнима» и не иметь исполнителя
нельзя по построению.

ЦЕНЫ. Директива говорит прямо: цены со скриншотов — предположения, их надо
проверять реальными бюджетами. Поэтому цены в каталоге нет вовсе: цена
появляется в сделке (состояние AGREED требует цену), а не в витрине.
"""
import importlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.db import connect, ensure_schema  # noqa: E402

SCHEMA = """
CREATE TABLE IF NOT EXISTS services (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL,              -- executable | not_yet
  buyer TEXT, problem TEXT, find_client TEXT, contact TEXT, offer TEXT,
  acceptance TEXT, executor TEXT, qa TEXT, delivery TEXT, payment TEXT, proof TEXT,
  reason TEXT,                       -- почему пока не умеем; у исполнимых пусто
  gnd_ref TEXT,                      -- где в директиве названа услуга
  checked_at TEXT NOT NULL
);
"""

REQUIRED = ("buyer", "problem", "find_client", "contact", "offer", "acceptance",
            "executor", "qa", "delivery", "payment", "proof")

FIELD_NAMES = {
    "buyer": "целевой покупатель", "problem": "доказанная проблема",
    "find_client": "способ найти клиента", "contact": "способ связаться",
    "offer": "коммерческое предложение", "acceptance": "критерии приёмки",
    "executor": "исполнитель", "qa": "QA", "delivery": "способ доставки",
    "payment": "способ оплаты", "proof": "доказательство каждого действия",
}

# Способ оплаты — список (валюта, сеть). Каждая пара сверяется с маршрутизатором.
CRYPTO = [("USDC", "base"), ("USDC", "polygon"), ("USDC", "arbitrum"),
          ("USDC", "ethereum"), ("BTC", "bitcoin")]

CATALOG = [
    {
        "name": "техническая документация",
        "gnd_ref": "§4 п.13, §6 documentation",
        "buyer": "сопровождающие открытых CLI-проектов, у которых нет справочника команд",
        "problem": "открытые задачи с меткой documentation и команды, не описанные нигде, "
                   "кроме исходника — это видно разбором дерева, а не догадкой",
        "find_client": "шаг find_doc_work: задачи GitHub с меткой documentation и "
                       "признаками CLI в исходниках",
        "contact": "публичная задача проекта на GitHub — один раз, по делу",
        "offer": "справочник команд, в котором каждое утверждение сверено с исходником",
        "acceptance": "каждая команда, аргумент и значение по умолчанию совпадают с "
                      "разбором исходника; ни одного утверждения без строки-источника",
        "executor": "agents.executor:produce",
        "qa": "agents.executor:verify",
        "delivery": "pull request в репозиторий заказчика",
        "payment": CRYPTO,
        "proof": "URL pull request, хеш коммита, список строк-источников",
    },
    {
        "name": "локализация документации",
        "gnd_ref": "§4 п.14, §6 localization",
        "buyer": "открытые проекты с задачами на перевод документации",
        "problem": "задачи на перевод, открытые без исполнителя; класс we_can_do их узнаёт",
        "find_client": "шаг find_doc_work, фильтр craftsman.we_can_do",
        "contact": "публичная задача проекта на GitHub",
        "offer": "перевод справочника, в котором имена команд и значения по умолчанию "
                 "перенесены из исходника, а не из перевода",
        "acceptance": "каждый технический факт перевода совпадает с исходником; "
                      "переведён только текст, а не имена",
        "executor": "agents.executor:produce",
        "qa": "agents.executor:verify",
        "delivery": "pull request в репозиторий заказчика",
        "payment": CRYPTO,
        "proof": "URL pull request и отчёт сверки",
    },
    {
        "name": "аудит совместимости x402",
        "gnd_ref": "§6 x402 integration, API QA",
        "buyer": "операторы платных x402-служб из каталога Bazaar",
        "problem": "служба объявляет версию 2 протокола, но читает только заголовок "
                   "версии 1 — заплатить ей клиент новой версии физически не может; "
                   "у нашей собственной службы это было",
        "find_client": "каталог Bazaar: 14 тысяч служб с адресами и ценой",
        "contact": "публичная задача в репозитории службы",
        "offer": "проверка, различающая «глуха к версии 2», «исправна» и «вывода нет», "
                 "с воспроизводимыми запросами",
        "acceptance": "вывод воспроизводится повторным запросом; подпись не настоящая и "
                      "денег не двигает",
        "executor": "core.x402_probe:check",
        "qa": "core.x402_probe:check_self",
        "delivery": "отчёт в публичной задаче или файлом",
        "payment": CRYPTO,
        "proof": "коды ответов на каждый заголовок и время замера",
    },
    {
        "name": "конкурентная разведка по каталогу x402",
        "gnd_ref": "§4 п.19, §6 competitive intelligence, market research",
        "buyer": "операторы платных служб, не знающие своего места среди конкурентов",
        "problem": "место службы по вызовам и плательщикам измеримо по каталогу, но "
                   "самим операторам не видно",
        "find_client": "salesman.diagnose по каталогу; outreach.rank_of считает место",
        "contact": "публичная задача в репозитории службы — один раз навсегда",
        "offer": "место среди служб своей категории по вызовам, плательщикам и цене",
        "acceptance": "каждое число выводится из каталога и воспроизводится",
        "executor": "agents.outreach:rank_of",
        "qa": "agents.outreach:verify_rank",
        "delivery": "отчёт в публичной задаче или файлом",
        "payment": CRYPTO,
        "proof": "URL обращения, снимок каталога, из которого посчитано место",
    },
    {
        "name": "извлечение и очистка данных",
        "gnd_ref": "§5 п.4 Data Extraction, §4 п.16-17, §6 data cleaning",
        "buyer": "те, кому нужна таблица из публичной страницы, JSON или CSV",
        "problem": "публичные запросы на выгрузку данных на досках задач",
        "find_client": "классы обхода «очистка данных», «таблицы и бизнес-данные»",
        "contact": "публичный канал, где размещён запрос",
        "offer": "CSV, JSON и XLSX с отчётом об источнике и числе удалённых дублей",
        "acceptance": "каждое значение найдено в исходнике дословно; записанные файлы "
                      "совпадают с отчётом по числу строк",
        "executor": "agents.extractor:produce",
        "qa": "agents.extractor:verify",
        "delivery": "файлы CSV/JSON/XLSX и sources.json",
        "payment": CRYPTO,
        "proof": "sha256 исходника, время снятия, отчёт sources.json",
    },
]

# ЧЕГО МЫ ПОКА НЕ УМЕЕМ — с причиной по каждой. Причина обязана называть
# недостающее, а не быть отговоркой: по ней видно, что надо сделать, чтобы
# строка перешла в исполнимые.
NOT_YET = {
    "ведение соцсетей": ("§5 п.1", "публикация требует авторизованного аккаунта заказчика; "
                                  "создавать аккаунты нам запрещено"),
    "поиск клиентов на заказ": ("§5 п.2", "для себя делаем (salesman, outreach), но передать "
                                          "контакты третьему лицу без согласия адресатов нельзя"),
    "поддержка пользователей": ("§5 п.3", "нужен доступ к почте или службе поддержки заказчика"),
    "управление репутацией": ("§5 п.5", "мониторинг упоминаний держится на бесплатном поиске, "
                                        "который отказывает; ответы требуют аккаунтов"),
    "SEO-блог": ("§5 п.6", "публикация и замер трафика требуют доступа к CMS и аналитике "
                           "заказчика"),
    "переработка подкастов": ("§5 п.7", "нет модели распознавания речи: whisper не установлен"),
    "персонализация холодных писем": ("§5 п.8", "отправка требует авторизованного ящика и "
                                                "согласия; собранные адреса запрещены"),
    "обработка PDF": ("§5 п.9", "текстовый слой читается (pypdf есть), но OCR нет: "
                                "tesseract не установлен, а визуальный QA без него не доказать"),
    "QA сайтов": ("§6", "исполнитель не написан: обход страниц есть только у агентов "
                        "разведки, отчёта для заказчика нет"),
    "QA API в общем виде": ("§6", "умеем только узкий случай — x402, он заведён отдельно"),
    "интеграция MCP": ("§6", "публикация в реестр идёт от пространства имён заказчика, "
                             "нужен его доступ"),
    "автоматизация процессов": ("§6", "исполнителя нет"),
    "база знаний": ("§6", "исполнителя нет; ближайшее — справочник команд"),
    "аудит доступности": ("§6", "нет инструмента проверки (axe-core не установлен)"),
    "технический SEO-аудит": ("§6", "исполнителя нет"),
    "обработка таблиц": ("§6", "частично покрыто извлечением данных; правка чужих таблиц "
                               "по заданию не написана"),
    "подготовка презентаций": ("§6", "исполнителя нет"),
    "мониторинг как продукт": ("§6", "наблюдатели есть (payment_watch, x402_probe), но нет "
                                     "доставки заказчику по подписке"),
    "разработка небольших утилит": ("§6", "код пишет improver, но только для себя и без "
                                          "прогона чужих тестов доказать правильность нельзя"),
    "разрешённое тестирование безопасности": ("§6", "требует границ программы и ручного "
                                                    "разрешения на каждую цель"),
    "сопровождение открытого кода": ("§6", "craftsman подаёт работу по баунти, но ни одна не "
                                           "принята — исполнимость не доказана"),
    "автоматизация онбординга клиентов": ("§6", "нужен доступ к продукту заказчика"),
}


def now():
    return datetime.now(timezone.utc).isoformat()


def _con():
    c = connect()
    ensure_schema(c, SCHEMA)
    return c


def _callable(ref):
    """Импортируется ли модуль:функция и вызывается ли она."""
    try:
        mod, fn = ref.split(":")
        obj = getattr(importlib.import_module(mod), fn)
    except (ValueError, ImportError, AttributeError) as e:
        return False, f"{ref}: {type(e).__name__}"
    return (True, "") if callable(obj) else (False, f"{ref}: не вызываемо")


def _payable(pairs):
    """Хотя бы одна пара (валюта, сеть) есть в маршрутизаторе и не закрыта."""
    from core import payment
    # Только чтение: маршруты заводит и проверяет сборщик платежей. Каталогу
    # услуг писать в чужую таблицу незачем — лишняя запись лишь повод для блокировки.
    live = {(r["currency"], r["network"]) for r in payment.routes()
            if r["status"] != "blocked"}
    ok = [p for p in pairs if tuple(p) in live]
    return ok, [p for p in pairs if tuple(p) not in live]


def check(svc):
    """Причины, по которым услуга НЕ исполнима. Пустой список — исполнима."""
    why = [f"нет поля «{FIELD_NAMES[f]}»" for f in REQUIRED if not svc.get(f)]
    for f in ("executor", "qa"):
        if svc.get(f):
            ok, err = _callable(svc[f])
            if not ok:
                why.append(f"{FIELD_NAMES[f]} не найден: {err}")
    if svc.get("payment"):
        ok, _ = _payable(svc["payment"])
        if not ok:
            why.append("ни один способ оплаты не совпал с маршрутом в core/payment.py")
    return why


def seed():
    """Заводит каталог. Исполнимость каждой строки проверяется при заведении."""
    c = _con()
    stamp = now()
    executable, demoted = 0, []
    for svc in CATALOG:
        why = check(svc)
        row = {f: (json.dumps(svc[f], ensure_ascii=False) if f == "payment" else svc.get(f))
               for f in REQUIRED}
        status = "not_yet" if why else "executable"
        if why:
            demoted.append(f"{svc['name']}: {'; '.join(why)}")
        else:
            executable += 1
        c.execute(f"""INSERT INTO services(name,status,{','.join(REQUIRED)},reason,gnd_ref,checked_at)
                      VALUES (?,?,{','.join('?' * len(REQUIRED))},?,?,?)
                      ON CONFLICT(name) DO UPDATE SET status=excluded.status,
                      {', '.join(f'{f}=excluded.{f}' for f in REQUIRED)},
                      reason=excluded.reason, gnd_ref=excluded.gnd_ref,
                      checked_at=excluded.checked_at""",
                  (svc["name"], status, *[row[f] for f in REQUIRED],
                   "; ".join(why) or None, svc.get("gnd_ref"), stamp))
    for name, (ref, reason) in NOT_YET.items():
        c.execute("""INSERT INTO services(name,status,reason,gnd_ref,checked_at)
                     VALUES (?,?,?,?,?)
                     ON CONFLICT(name) DO UPDATE SET status='not_yet', reason=excluded.reason,
                     gnd_ref=excluded.gnd_ref, checked_at=excluded.checked_at""",
                  (name, "not_yet", reason, ref, stamp))
    c.commit()
    c.close()
    return {"исполнимых": executable, "не прошли проверку": demoted,
            "пока не умеем": len(NOT_YET)}


def report():
    """Каталог одной строкой — для агентов и дашборда."""
    res = seed()
    line = (f"услуг исполнимых {res['исполнимых']} из {len(CATALOG)} заявленных; "
            f"пока не умеем {res['пока не умеем']}")
    if res["не прошли проверку"]:
        line += "; НЕ ПРОШЛИ ПРОВЕРКУ: " + " | ".join(res["не прошли проверку"])
    return line


def listing():
    c = _con()
    rows = c.execute("SELECT name,status,executor,reason,gnd_ref FROM services "
                     "ORDER BY status, name").fetchall()
    c.close()
    return [dict(zip(("name", "status", "executor", "reason", "gnd_ref"), r)) for r in rows]


if __name__ == "__main__":
    print(report())
    for s in listing():
        mark = "ИСПОЛНИМА " if s["status"] == "executable" else "пока нет  "
        print(f"  {mark} {s['name']:<42} {s['executor'] or s['reason']}")
