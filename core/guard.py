"""Blast radius controls. Limits live in CODE, not in prompts - prompts can be
argued with, code cannot. DECISION_PROTOCOL.md section 5."""
from pathlib import Path
from datetime import date
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.db import connect

KILL_SWITCH = Path(__file__).resolve().parent.parent / "data" / "KILL_SWITCH"
CAPS = {"email_send": 200, "email_tag": 200, "page_publish": 20, "public_post": 10,
        # РАЗВЁРТЫВАНИЕ ПЛАТНОГО СЕРВИСА АГЕНТОМ. Владелец разрешил 17.09, но частота
        # тут важнее разрешения: цикл, который деплоит в петле, уронит выручку быстрее,
        # чем кто-нибудь это заметит. Четыре за сутки — это четыре попытки починить
        # что-то реальное, а не способ жить в состоянии постоянного развёртывания.
        "deploy_service": 4,
        # Moltbook разрешён владельцем как общий публичный канал. Эти пределы
        # намеренно ниже лимитов площадки: доступ не должен превращаться в шум.
        "moltbook_publish": 1, "moltbook_comment": 5,
        "moltbook_reply": 5, "moltbook_edit": 5,
        # Заявка на задачу — публичное действие в чужом репозитории. Предел низкий
        # намеренно: на живом рынке мы намерили 72 заявки на одной задаче за $75,
        # из которых работу прислали единицы. Заявка без готовой работы — это шум,
        # который мы сами же и осуждаем, а не участие.
        "bounty_claim": 3,
        # Отправка PR в чужой репозиторий. Столько же: больше трёх за сутки мы
        # физически не сделаем на уровне, который сольют.
        "pr_submit": 3,
        # Обращения дилера к контрагентам с деньгами: один контрагент — одно
        # обращение навсегда, и не больше пяти в сутки.
        "dealer_message": 5,
        # Сдача на AIBTC — публичное действие подписью агента; больше трёх в сутки не сделаем на уровне.
        "aibtc_submit": 3}
CLASSES = {"GREEN","YELLOW","RED","BLACK"}

# Что мы ПРОДАЁМ. Если это оказалось в открытом доступе — мы раздаём собственный товар.
# Проверки на это не было, и полный датасет за $1.25 пролежал бесплатно, пока владелец
# не заметил сам. Ни один агент не поймал: никто и не смотрел.
from core.identity import TIERS as _TIERS   # цены объявлены один раз, см. core/identity.py

PAID_PRODUCTS = {
    "полный датасет": {"paths": ["docs/x402-market.json", "docs/x402-market.csv"],
                       "price": _TIERS["/dataset"], "endpoint": "/dataset"},
    "отчёт по рынку": {"paths": ["docs/report.json", "docs/market-report.json"],
                       "price": _TIERS["/report"], "endpoint": "/report"},
    "анализ ниш":     {"paths": ["docs/alpha.json", "docs/opportunities.json"],
                       "price": _TIERS["/alpha"], "endpoint": "/alpha"},
    "бенчмарк цен":   {"paths": ["docs/price-benchmark.json"],
                       "price": _TIERS["/price"], "endpoint": "/price"},
    "разрез по сетям": {"paths": ["docs/networks.json"],
                       "price": _TIERS["/networks"], "endpoint": "/networks"},
}
PUBLIC_DIRS = ["docs"]
MAX_FREE_SAMPLE_ROWS = 150          # больше — это уже не образец, а продукт


class GivingAwayProduct(Exception): pass


class Halted(Exception): pass
class CapExceeded(Exception): pass
class Forbidden(Exception): pass

def check_alive():
    if KILL_SWITCH.exists():
        raise Halted("KILL_SWITCH present - all agent action halted.")

def check_paid_product_not_public():
    """Не выложен ли платный товар бесплатно. Запускается в каждом аудите
    и перед любой публикацией."""
    import json
    root = Path(__file__).resolve().parent.parent
    violations = []
    for name, spec in PAID_PRODUCTS.items():
        for rel in spec["paths"]:
            f = root / rel
            if f.exists():
                violations.append(f"{name} (${spec['price']}, тариф {spec['endpoint']}) "
                                  f"лежит открыто: {rel}")
    # любой крупный набор данных в публичной папке подозрителен
    for d in PUBLIC_DIRS:
        pub = root / d
        if not pub.exists():
            continue
        for f in pub.rglob("*"):
            if f.suffix.lower() not in (".json", ".csv") or not f.is_file():
                continue
            if "sample" in f.name.lower() or "stats" in f.name.lower():
                continue
            try:
                if f.suffix.lower() == ".json":
                    data = json.loads(f.read_text(encoding="utf-8"))
                    n = len(data) if isinstance(data, list) else 0
                else:
                    n = sum(1 for _ in f.open(encoding="utf-8")) - 1
            except Exception:
                continue
            if n > MAX_FREE_SAMPLE_ROWS:
                violations.append(f"{f.relative_to(root)}: {n} строк в открытой папке "
                                  f"(предел образца {MAX_FREE_SAMPLE_ROWS})")
    return violations


# ═══════════════════════ ПОСТОЯННЫЕ РАЗРЕШЕНИЯ ВЛАДЕЛЬЦА
#
# Разрешение, выданное в переписке и нигде не записанное, через неделю
# неотличимо от самоуправства. Поэтому оно живёт здесь, с датой и словами, а
# не в чьей-то памяти: любой, кто позже спросит «кто им позволил», найдёт ответ
# в коде, а не будет его восстанавливать.
#
# Что разрешение МЕНЯЕТ: агент больше не спрашивает перед каждым разом.
# Чего оно НЕ МЕНЯЕТ: заслонов качества. Они защищают не от владельца, а от
# позора в чужом репозитории — заявка в задачу с семьюдесятью претендентами
# или с планом «сделаю» портит нашу репутацию, а восстанавливать её дороже,
# чем заработать первый доллар.
STANDING_PERMISSIONS = {
    "lead_reply": {
        "выдано": "2026-09-16",
        "кем": "владелец, прямыми словами",
        "слова": "IT SHOULDN'T BE DELAYED; AI agents should be able to find a workaround and find a solution",
        "что именно": "ответ в уже открытом нами обсуждении на реплику лида — по шаблону из фактов "
                      "(предложение, цена, способ оплаты, проверка), без обещаний",
        "пределы, которые остаются": [
            "только в обсуждениях, которые начали мы; первым повторно не пишем",
            "только на реплики участников репозитория (OWNER/MEMBER/COLLABORATOR)",
            "текст — шаблон из фактов; свободный текст локальная модель не пишет",
            "отказ или закрытое обсуждение — без реплики, сделка закрывается",
        ],
    },
    "self_deploy": {
        "выдано": "2026-09-17",
        "кем": "владелец, прямыми словами",
        "слова": "AI AGENTS SHOULD BE DOIN THIS NOT YOU, IMAGINE THAT YOU'RE OFF; "
                 "I GIVE YOU PERMISSION FOR ALL YOU CAN DO IT AND LEARN AI AGENTS "
                 "TO DO IT WITHOUT INVOLVING HUMAN",
        "что именно": "агент сам разворачивает платный сервис: прогоняет тесты, сверяет адрес "
                      "получателя в бандле, деплоит, ждёт разноса версии и проверяет живьём; "
                      "перестал продавать — сам откатывается на здоровую версию",
        "пределы, которые остаются": [
            "адрес получателя в бандле обязан совпасть с нашим — иначе развёртывание "
            "отменяется, и это единственная проверка, которую нельзя ослабить",
            "красные тесты (питон ИЛИ воркер) — развёртывания нет; пустой сбор тестов "
            "считается красным, а не зелёным",
            "после развёртывания — пауза на разнос и три единогласных живых проверки; "
            "одна неудача = откат",
            "не больше четырёх развёртываний в сутки (CAPS deploy_service)",
            "откат ищет версию, которая действительно продаёт; не нашлось — честно "
            "сказать об этом, а не отрапортовать успех",
        ],
    },
    "taskmarket_submit": {
        "выдано": "2026-09-15",
        "кем": "владелец, прямыми словами",
        "слова": "THESE DECISIONS CAN BE MADE BY AI AGENTS IF ... FREE ... ACTUAL TRANSACTION PAYMENT; "
                 "IT SHOULDN'T BE DELAYED",
        "что именно": "подача работы на открытую эскроу-задачу Taskmarket нашего класса от кошелька-исполнителя",
        "пределы, которые остаются": [
            "одна подача на задачу (первые пять бесплатны — вторую не делаем)",
            "только после механических ворот из брифа: вид файла, пределы, заголовок, живые источники",
            "только текстовые и табличные результаты; плакаты, иллюстрации, ручные тесты — не наше",
        ],
    },
    "taskmarket_withdraw": {
        "выдано": "2026-09-15",
        "кем": "владелец, прямыми словами",
        "слова": "THESE DECISIONS CAN BE MADE BY AI AGENTS IF IT CAN BE DONE AND TOTALLY FREE "
                 "TO USE AND WE CAN RECEIVE ACTUAL TRANSACTION PAYMENT ON THAT",
        "что именно": "вывод заработанных USDC с кошелька-исполнителя Taskmarket на кошелёк владельца "
                      "(адрес вывода зарегистрирован одноразово, иной CLI не примет; газ платит площадка)",
        "пределы, которые остаются": [
            "только на зарегистрированный адрес владельца — другого назначения у команды нет",
            "только заработанное: кошелёк-исполнитель никогда не пополняется",
            "каждый вывод — находка с хэшем перевода на общей доске",
        ],
    },
    "bounty_claim": {
        "выдано": "2026-09-12",
        "кем": "владелец, прямыми словами",
        "слова": "ОБНОВИ ПРАВИЛО И ДАЙ АГЕНТАМ РАЗРЕШЕНИЕ НАВСЕГДА ДА!",
        "что именно": "публично заявлять права на задачу с наградой в чужом "
                      "репозитории от имени аккаунта владельца",
        "пределы, которые остаются": [
            "не больше трёх заявок в сутки",
            "только задачи, где награду объявил САМ ПРОЕКТ (declared=1)",
            "только классы работы, которые мы можем доказать построчно",
            "не больше трёх соперников: в толпе заявка — шум",
            "план обязан называть конкретные файлы, а не обещать «сделаю»",
        ],
    },
    "dealer_message": {
        "выдано": "2026-09-15",
        "кем": "владелец, прямыми словами",
        "слова": "I GIVE YOU PERMISSION, DO THE BEST AVAILABLE AND FITTABLE CHOICE; "
                 "FIND A WAY AROUND IT ... DON'T LIMIT YOURSELF",
        "что именно": "обращение дилера к контрагенту с деньгами по протоколу MTBX (шесть "
                      "элементов, адрес кошелька там, где он живёт по замеру) от общей "
                      "учётной записи",
        "пределы, которые остаются": [
            "один контрагент — одно обращение навсегда",
            "не больше пяти обращений в сутки; пределы площадки (Moltbook CAPS) действуют",
            "адрес кошелька в тексте — только в сабмолтах с измеренной политикой",
            "класс BLACK неизменен: аккаунты, KYC, капчи против роботов, траты, движение средств",
        ],
    },
    "moltbook_publish": {
        "выдано": "2026-09-13",
        "кем": "владелец, прямыми словами",
        "слова": "ensure they are aware and communicating there when they're live",
        "что именно": "одна содержательная неплатёжная техническая публикация от общей "
                      "учётной записи с указанием внутренней роли",
        "пределы, которые остаются": ["не больше одной публикации в сутки",
                                      "без ссылок, рекламы, криптопродвижения и повторов",
                                      "публикация считается состоявшейся только после readback"],
    },
    "moltbook_comment": {
        "выдано": "2026-09-13", "кем": "владелец, прямыми словами",
        "слова": "access read comment edit reply in moltbook",
        "что именно": "ответить по существу на конкретный технический вопрос",
        "пределы, которые остаются": ["не больше пяти в сутки", "не писать повторно",
                                      "не рекламировать"],
    },
    "moltbook_reply": {
        "выдано": "2026-09-13", "кем": "владелец, прямыми словами",
        "слова": "access read comment edit reply in moltbook",
        "что именно": "ответить на конкретный комментарий по существу",
        "пределы, которые остаются": ["не больше пяти в сутки", "без массового outreach"],
    },
    "moltbook_verification": {
        "выдано": "2026-09-13", "кем": "владелец, прямыми словами",
        "слова": "КОНЕЧНО ДА А ЗАЧЕМ ЕЩЕ МЫ ЭТО ДЕЛАЛИ?",
        "что именно": "решать проверочную арифметическую задачу Moltbook, которую площадка "
                      "сама ставит ИИ-агентам перед публикацией (это не капча против роботов)",
        "пределы, которые остаются": ["решатель отвечает только когда уверен",
                                      "предохранитель на шести провалах подряд (бан на десяти)"],
    },
    "owner_blanket": {
        "выдано": "2026-09-14", "кем": "владелец, прямыми словами",
        "слова": "не нужно меня просить ДАЮ РАЗРЕШЕНИЕ! ВСЕ ИИ АГЕНТЫ ПОЛУЧАЮТ РАЗРЕШЕНИЕ!",
        "что именно": "не спрашивать владельца перед действиями разрешённых классов: "
                      "обращения и ответы лидам, заявки, публикации и комментарии, подписки, "
                      "доставка работы; очередь суждений остаётся ТОЛЬКО для текста, который "
                      "локальная модель не пишет (ответ лиду) — это не запрос разрешения",
        "пределы, которые остаются": [
            "класс BLACK неизменен: аккаунты, KYC, капчи против роботов, траты, движение средств",
            "правила площадок (Moltbook: без навязчивого самопродвижения; GitHub: без спама)",
            "суточные пределы CAPS — защита от шума, а не от владельца",
        ],
    },
    "moltbook_edit": {
        "выдано": "2026-09-13", "кем": "владелец, прямыми словами",
        "слова": "access read comment edit reply in moltbook",
        "что именно": "исправлять только собственную публикацию общей учётной записи",
        "пределы, которые остаются": ["чужие записи неизменяемы",
                                      "успех только при точном readback"],
    },
}


def standing(kind):
    """Есть ли постоянное разрешение владельца на такое действие."""
    return STANDING_PERMISSIONS.get(kind)


def _note_standing_use(kind, granted):
    """Отмечает пользование постоянным разрешением — молча им не пользуются.

    Разрешение «навсегда» опасно тем, что перестаёт быть заметным. Запись в
    журнал возвращает видимость: владелец в любой момент может посмотреть, что
    именно делалось по его разрешению, и отозвать его, если увиденное не
    нравится.
    """
    try:
        from core.db import connect
        from datetime import datetime, timezone
        c = connect()
        c.execute("CREATE TABLE IF NOT EXISTS permission_uses ("
                  "id INTEGER PRIMARY KEY, kind TEXT, granted_at TEXT, at TEXT)")
        c.execute("INSERT INTO permission_uses(kind,granted_at,at) VALUES (?,?,?)",
                  (kind, granted.get("выдано"),
                   datetime.now(timezone.utc).isoformat()))
        c.commit(); c.close()
    except Exception:
        pass


def check_action(kind, action_class):
    check_alive()
    if action_class == "BLACK":
        raise Forbidden(f"'{kind}' is BLACK: agents may never do this (accounts, "
                        f"KYC, CAPTCHA, spending, moving funds).")
    if action_class not in CLASSES:
        raise Forbidden(f"unknown action class {action_class!r}")
    if kind in ("publish_dataset", "page_publish", "publish"):
        v = check_paid_product_not_public()
        if v:
            raise GivingAwayProduct("публикация отменена — раздаём платное: " + "; ".join(v))
    # Постоянное разрешение снимает вопрос «спрашивать ли», но не снимает
    # пределов: у владельца есть право позволить, а не право сделать нас
    # источником шума.
    granted = standing(kind)
    if granted:
        _note_standing_use(kind, granted)

    cap = CAPS.get(kind)
    if cap is not None:
        con = connect()
        n = con.execute(
            "SELECT COUNT(*) FROM actions WHERE kind=? AND dry_run=0 AND date(created_at)=?",
            (kind, date.today().isoformat())).fetchone()[0]
        if n >= cap:
            raise CapExceeded(f"'{kind}' hit today's cap ({cap}). Agents cannot exceed this.")
    return True
