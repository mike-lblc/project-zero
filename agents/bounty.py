"""ОХОТНИК ЗА БАУНТИ — работа, за которую платят живые деньги.

Почему это найдено так поздно: я всё время искал, КОМУ ПРОДАТЬ, и ни разу не
посмотрел, ГДЕ ПЛАТЯТ ЗА РАБОТУ НАПРЯМУЮ.

Порядок величин, ради которого стоит развернуться:
    рынок x402, лучшая ниша .......... $2.61 на поставщика в МЕСЯЦ
    один баунти ...................... $50 – $2500 за ЗАДАЧУ
Один баунти в $500 равен двумстам годам медианной выручки на x402.

Почему это подходит именно нам:
    * платят за КОД — ровно то, что мы умеем
    * никаких холодных писем: решил задачу, прислал PR, получил деньги
    * оплата часто криптой на кошелёк, то есть рельс тот же, что уже проверен
    * `gh` уже авторизован, форк и PR доступны прямо сейчас

Что здесь честно ограничено:
    * берём только задачи, где сумма указана ЯВНО в заголовке или теле
    * отсеиваем мусорные репозитории (нулевые звёзды + свежесозданные + серии
      однотипных «баунти» — типичная накрутка)
    * не обещаем выплату: она зависит от площадки и от слияния PR
"""
import sys, re, json, subprocess
from pathlib import Path
from datetime import datetime, timezone, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.db import connect, ensure_schema
from core import guard, bus

ROOT = Path(__file__).resolve().parent.parent

SCHEMA = """
CREATE TABLE IF NOT EXISTS bounties (
  id INTEGER PRIMARY KEY,
  url TEXT NOT NULL UNIQUE,
  repo TEXT NOT NULL,
  title TEXT NOT NULL,
  amount_usd REAL,
  currency TEXT,
  stars INTEGER,
  language TEXT,
  labels TEXT,
  fit_score REAL,
  rivals INTEGER DEFAULT 0,
  payout TEXT,
  -- ОБЪЯВИЛ ЛИ НАГРАДУ САМ ПРОЕКТ. Признак вычислялся и терялся сразу
  -- после оценки, поэтому дальше по конвейеру никто не мог отличить
  -- объявленную награду от суммы, которую написал посторонний.
  -- КОГДА ЗАПИСЬ ПОДТВЕРЖДЕНА В ПОСЛЕДНИЙ РАЗ. Без этого столбца находка,
  -- которую источник подтверждает каждый час, выглядела вчерашней: мы
  -- писали только дату ПЕРВОЙ встречи, и со стороны казалось, что поиск
  -- стоит на месте.
  last_seen TEXT,
  prizes INTEGER,                    -- у конкурса: число призовых мест
  declared INTEGER,
  declared_proof TEXT,
  status TEXT NOT NULL DEFAULT 'found',   -- found | shortlisted | attempted | won | lost
  note TEXT,
  found_at TEXT NOT NULL
);
"""

# Языки и темы, где мы реально можем сделать работу, а не сделать вид
OUR_STACK = {"python": 1.0, "javascript": 0.9, "typescript": 0.9, "shell": 0.8,
             "html": 0.7, "sql": 0.8, "go": 0.5, "rust": 0.35, "c": 0.2, "c++": 0.2,
             "java": 0.4, "solidity": 0.4}

# Признаки фермы баунти. Первая версия отсекала только репозитории без звёзд,
# и пропустила ферму с 8 звёздами, где ОДИН репозиторий держал пять «задач»
# на $500-1250 — то есть 74% найденной суммы оказалось мусором.
SPAM_HINTS = ("bounty-", "-bounty", "bounties", "airdrop", "reward-hub",
              "-test", "test-", "playground-farm", "radar")
AGGREGATOR_HINTS = ("bounty alert", "bounty-alert", "new opportunities", "bountyscout",
                    "opportunities digest", "weekly bounties", "bounty digest")
MIN_STARS_FOR_BIG = 40      # крупная сумма от малоизвестного репозитория недостоверна
# Нижний предел известности для ЛЮБОЙ суммы. Репозиторий, не нашедший и десятка
# читателей, вряд ли найдёт деньги: метка bounty на своей же задаче ничего не
# стоит её автору. Поймано на живой заявке — она ушла в репозиторий с одной
# звездой, чьё имя дословно значит «тестовый аккаунт».
MIN_STARS_ANY = 10
# До этой суммы рискнуть можно и с неизвестным репозиторием: цена ошибки — час
# работы исполнителя, а мелкие задачи почти всегда там и живут. Выше — нужна
# либо известность, либо след платёжной площадки.
SMALL_ENOUGH_TO_RISK = 30.0
BIG_AMOUNT = 200.0
MAX_PER_REPO = 2            # ферма выдаёт десятки однотипных задач из одного места
MAX_PER_REPO_OFFSITE = 8    # агрегатор чужих конкурсов фермой не является

AMOUNT_RE = [
    (re.compile(r"\$\s?([\d,]+(?:\.\d+)?)\s*(?:k\b)?", re.I), 1.0),
    (re.compile(r"([\d,]+(?:\.\d+)?)\s*USDC?\b", re.I), 1.0),
    (re.compile(r"bounty[:\s]+\$?\s?([\d,]+)", re.I), 1.0),
]


def now():
    return datetime.now(timezone.utc).isoformat()


def _con():
    c = connect()
    ensure_schema(c, SCHEMA)
    return c


_API_TROUBLE = []          # накопленные отказы API за текущий заход


def _gh(args, timeout=40, expect_missing=False):
    """Вызов gh. Отказ API запоминается, а не выдаётся за пустой ответ.

    expect_missing=True — для запросов, где «нет такого файла» это нормальный
    ответ, а не сбой (мы наугад щупаем CONTRIBUTING.md по трём путям). Без
    этого различия ожидаемый 404 объявлял весь заход несостоявшимся.

    Раньше любая ошибка превращалась в "", и заход рапортовал «работы нет»,
    хотя на самом деле GitHub просто отказал по лимиту поиска (30 запросов
    в минуту). Молчание, выданное за отсутствие работы, — это ровно тот
    вид вранья, который спецификация запрещает.
    """
    try:
        r = subprocess.run(["gh"] + args, capture_output=True, text=True, timeout=timeout)
    except (subprocess.SubprocessError, OSError) as e:
        _API_TROUBLE.append(type(e).__name__)
        return ""
    if r.returncode == 0:
        return r.stdout
    err = (r.stderr or "") + (r.stdout or "")
    if expect_missing and "Not Found" in err:
        return ""
    if "rate limit" in err.lower():
        _API_TROUBLE.append("лимит запросов GitHub исчерпан")
    elif "403" in err or "401" in err:
        _API_TROUBLE.append("доступ к API отклонён")
    else:
        _API_TROUBLE.append(err.strip()[:60] or "неизвестная ошибка gh")
    return ""


def competition(repo, issue_number):
    """Сколько человек УЖЕ делают эту задачу и не выплачена ли она.

    Возвращает (соперники, выплачена_ли).

    Первая версия искала открытые PR, чьи заголовки содержат слова из
    заголовка задачи, и на реальном примере выдала «соперников 0» для
    tscircuit#92, где в обсуждении лежало больше сотни заявок `/attempt`,
    два десятка присланных PR и комментарий бота о том, что $75 УЖЕ
    выплачены другому человеку. Заголовки PR просто не совпадали со
    словами задачи — и агент чуть не вложил работу в разобранную задачу.

    Считать надо по самому обсуждению: заявки и ссылки на PR лежат там.
    """
    if not issue_number:
        return 0, False
    out = _gh(["api", f"repos/{repo}/issues/{issue_number}/comments?per_page=100",
               "--jq", '[.[]|{u:.user.login, b:.body}] | @json'], timeout=45)
    if not out:
        return 0, False
    try:
        comments = json.loads(out.strip().split("\n")[0])
    except (json.JSONDecodeError, IndexError):
        return 0, False

    claimants, paid = set(), False
    for c in comments:
        b = (c.get("b") or "")
        low = b.lower()
        # бот площадки объявляет о выплате — задача закрыта деньгами
        if "has been awarded" in low or "you've been awarded" in low \
                or "you have been awarded" in low:
            paid = True
        if re.search(r"/attempt\b|/claim\b|/start\b|/opire try\b", low) \
                or "github.com/" in low and "/pull/" in low:
            claimants.add(c.get("u") or b[:20])
    return len(claimants), paid


# Способы выплаты и доходят ли они до РФ-резидента с кошельком.
# ЭТОТ ФИЛЬТР ВАЖНЕЕ СУММЫ. Урок повторился дважды: сначала Binance
# (рынок есть, деньги не дойдут), теперь omi ($25 есть, платят PayPal).
# Проверять «чем платят» надо ДО того, как вложена работа.
# ОСОЗНАННОЕ ОТСТУПЛЕНИЕ ОТ GND §3. Директива требует отклонять работу только
# после установленной недоступности выплаты. Для фиата мы отклоняем сразу, по
# решению владельца: он резидент РФ, и PayPal, Stripe и банковский перевод ему
# недоступны фактически, а не предположительно. Это не недосмотр — отступление
# записано здесь и отдельной строкой в итоговом отчёте (ops/gnd_report.py).
PAYOUT_BLOCKED = ("paypal", "venmo", "zelle", "cashapp", "ach ", "wire transfer",
                  "bank transfer", "stripe")

# ДОЙДУТ ЛИ ДЕНЬГИ — ОТВЕЧАЕТ МАРШРУТИЗАТОР, А НЕ СПИСОК СЛОВ.
#
# Здесь был список «хороших» слов, и у него было два дефекта. Первый: он
# расходился с core/payment.py — Algora считалась «деньги дойдут», а
# маршрутизатор знает, что маршрут Algora не проверен (нужен аккаунт
# владельца). Второй: слова искались подстрокой, и «eth» совпадало с
# «method» — любое описание со словом «method» объявлялось достижимой выплатой.
#
# Теперь слово лишь называет способ, а ответ «да / нет / неизвестно» берётся
# из маршрутизатора — того же, что принимает деньги.
PAYOUT_PLATFORMS = {"algora": "algora", "polar.sh": "polar", "gitcoin": "gitcoin"}
# Ликвидные крипто-активы на наших сетях. Оплата НЕ обязана быть в USDC —
# принимаем ETH, BTC, POL и ходовые токены; адреса кошельков есть под все.
PAYOUT_CURRENCIES = {"usdc": "USDC", "usdt": "USDT", "dai": "USDC", "weth": "ETH",
                     "matic": "POL", "pol": "POL", "eth": "ETH", "ether": "ETH",
                     "btc": "BTC", "bitcoin": "BTC", "polygon": "POL"}
PAYOUT_WALLET_WORDS = ("crypto", "wallet address", "onchain", "on-chain", "stablecoin")


def _router_answer(text):
    """Ответ маршрутизатора по словам о способе выплаты. None — не названо или неизвестно."""
    from core import payment
    answers = []
    for word, provider in PAYOUT_PLATFORMS.items():
        if re.search(rf"\b{re.escape(word)}\b", text):
            answers.append(payment.reachable(provider=provider)[0])
    for word, currency in PAYOUT_CURRENCIES.items():
        if re.search(rf"\b{word}\b", text):
            answers.append(payment.reachable(currency=currency)[0])
    if any(w in text for w in PAYOUT_WALLET_WORDS):
        answers.append(True if any(r["status"] == "verified" and r["provider"] == "self-custody"
                                   for r in payment.routes()) else None)
    if True in answers:
        return True
    if answers and all(a is False for a in answers):
        return False
    return None


def _opire_reward(repo, number):
    """Сумма наград Opire по issue: бот opirebot пишет «created a $40.00 reward».
    Складываем все награды (у одной issue их может быть несколько); нет — None."""
    if not repo or not number:
        return None
    out = _gh(["api", f"repos/{repo}/issues/{number}/comments?per_page=100",
               "--jq", '[.[] | select(.user.login | test("opire";"i")) | .body] | join(" ")'])
    if not out:
        return None
    total = 0.0
    for m in re.finditer(r"\$\s?([\d,]+(?:\.\d+)?)\s*reward", out, re.I):
        try:
            total += float(m.group(1).replace(",", ""))
        except ValueError:
            continue
    return total if 5 <= total <= 50000 else None


# Кто в проекте имеет право объявлять награду. GitHub отдаёт это в поле
# author_association: посторонний помечен как NONE или CONTRIBUTOR.
AUTHORITY = {"OWNER", "MEMBER", "COLLABORATOR"}


def declared_by_project(repo, issue_number):
    """Объявил ли награду САМ ПРОЕКТ. Возвращает (да/нет/неизвестно, чем доказано).

    ЗАЧЕМ ЭТА ПРОВЕРКА ПОЯВИЛАСЬ — на нашей же ошибке, стоившей дня работы.
    Мы написали руководство и сочли его работой на $25. Сумма стояла в
    заголовке задачи, и этого показалось достаточно. На деле задачу завёл
    такой же соискатель, а её текст был ВОПРОСОМ к мейнтейнерам: «подойдёт ли
    награда в $25?» — и ответа не последовало ни разу. Меток награды у задачи
    не было вовсе, а последняя задача с такой меткой заведена за восемь с
    половиной месяцев до того дня.

    Различение простое и проверяемое: награду объявляет проект — меткой или
    словами человека с правами в репозитории. Сумма, написанная посторонним,
    это ПРЕДЛОЖЕНИЕ цены, а не цена. Работать по ней можно, но называть это
    оплачиваемой работой нельзя: тогда мы обманываем себя о собственной
    выручке, а это дороже любого потерянного дня.

    Возвращает None, если выяснить не удалось. Неизвестность — не отказ:
    отсутствие ответа API мы уже однажды доложили как отсутствие работы.
    """
    raw = _gh(["api", f"/repos/{repo}/issues/{issue_number}"])
    if not raw:
        return None, "задача не прочиталась — это незнание, а не отказ"
    try:
        issue = json.loads(raw)
    except ValueError:
        return None, "ответ по задаче не разобрался"

    labels = [str(l.get("name", "")).lower() for l in (issue.get("labels") or [])]
    money = [l for l in labels
             if "bounty" in l or "reward" in l or "\U0001f4b0" in l or "\U0001f48e" in l]
    if money:
        return True, f"метка проекта: {money[0]}"

    opener = (issue.get("author_association") or "").upper()
    if opener in AUTHORITY:
        return True, f"задачу завёл человек с правами: {opener}"

    # Меток нет и завёл посторонний — остаётся слово мейнтейнера в обсуждении.
    # Именно его у нас и не было: на шестнадцати таких же задачах ноль ответов.
    raw_c = _gh(["api", f"/repos/{repo}/issues/{issue_number}/comments?per_page=100"])
    if not raw_c:
        return None, "обсуждение не прочиталось"
    try:
        comments = json.loads(raw_c)
    except ValueError:
        return None, "обсуждение не разобралось"
    for c in comments:
        if (c.get("author_association") or "").upper() in AUTHORITY:
            who = (c.get("user") or {}).get("login")
            body = " ".join((c.get("body") or "")[:130].split())
            return True, f"подтвердил {who}: {body}"

    return False, (f"НАГРАДУ НИКТО НЕ ОБЪЯВЛЯЛ: меток нет, задачу завёл "
                   f"{opener or 'посторонний'}, мейнтейнеры не отвечали "
                   f"({len(comments)} комментариев)")


def payout_reachable(repo, body):
    """Дойдут ли деньги. True / False / None (неизвестно).

    None означает «не выяснили» — тогда работу вкладывать рано, но и
    отбрасывать нельзя: надо спросить у мейнтейнера.
    """
    b = (body or "").lower()
    if any(w in b for w in PAYOUT_BLOCKED):
        return False
    ans = _router_answer(b)
    if ans is not None:
        return ans
    # правила проекта: смотрим руководство для участников
    for path in ("docs/doc/developer/Contribution.mdx", "CONTRIBUTING.md",
                 ".github/CONTRIBUTING.md"):
        out = _gh(["api", f"repos/{repo}/contents/{path}", "--jq", ".content"],
                  timeout=25, expect_missing=True)
        if not out:
            continue
        try:
            import base64
            txt = base64.b64decode(out).decode("utf-8", "ignore").lower()
        except Exception:
            continue
        # Ищем ТОЛЬКО вокруг слов о выплате. Первая версия искала по всему
        # документу и сказала «дойдут» для проекта, где платят PayPal, —
        # потому что слово «wallet» там про носимое устройство, а не про деньги.
        zones = []
        for m in re.finditer(r"(claim\w*\s+payment|payout|get paid|bounty rules|"
                             r"claiming payment|reward)", txt):
            zones.append(txt[max(0, m.start() - 200): m.start() + 400])
        near = " ".join(zones)
        if not near:
            break
        if any(w in near for w in PAYOUT_BLOCKED):
            return False
        ans = _router_answer(near)
        if ans is not None:
            return ans
        break
    return None


def looks_like_real_money(body, labels):
    """Настоящая ли это оплата.

    Проверено на живых примерах:
      /bounty $75          -> команда Algora, НАСТОЯЩИЕ деньги
      «25 RTC»             -> собственный токен проекта, НЕ доллары
      тело без суммы вовсе -> тренировочная песочница (9446 автозадач)
    """
    b = (body or "").lower()
    # МЕТКА ПЛОЩАДКИ. Параметр labels принимался и молча выбрасывался — а это
    # был самый достоверный признак из всех: метку «💎 Bounty» ставит сам бот
    # выплат. Задачи, где сумма указана только меткой, отсеивались как мусор.
    lab = " ".join(labels or []).lower()
    if "bounty" in lab or "reward" in lab or "💎" in lab or "💰" in lab or "opire" in lab:
        return True
    if "/bounty" in b or "algora" in b:
        return True
    if re.search(r"\$\s?\d", b):
        return True
    if re.search(r"\d+\s*(rtc|points?|credits?|tokens?)", b):
        return False          # своя валюта, не деньги
    return False


def parse_amount(text):
    """Сумма из заголовка или тела. Берём максимум, но не верим абсурду."""
    best = 0.0
    for rx, mult in AMOUNT_RE:
        for m in rx.finditer(text or ""):
            try:
                v = float(m.group(1).replace(",", "")) * mult
            except Exception:
                continue
            # ПОРОГ СНИЖЕН ДО ПЯТИ ДОЛЛАРОВ ПО РЕШЕНИЮ ВЛАДЕЛЬЦА.
            #
            # Здесь стояло двадцать, и обоснование было моим, а не измеренным:
            # «ниже не окупает время». Владелец возразил прямо, и он прав по
            # арифметике миссии — мы доказываем, что заработать можно с нуля, а
            # не что заработать можно много. Пять долларов, полученные от
            # постороннего, доказывают ровно то же, что пятьсот, и приходят
            # гораздо чаще.
            #
            # Замер это подтвердил: на живой выдаче GitHub задачи «/bounty $10»
            # и «/tip $5» отбрасывались молча, и в воронке они числились как
            # «сумма не разобралась» — то есть выглядели ошибкой разбора, а не
            # решением порога. Потолок оставлен: выше пятидесяти тысяч почти
            # всегда мусор или призовой фонд конкурса, а не задача.
            if 5 <= v <= 50000:
                best = max(best, v)
    return best or None


def search(limit=60):
    """Ищет открытые оплачиваемые задачи по нашему стеку."""
    guard.check_action("research", "GREEN")
    # Запросы отранжированы ИЗМЕРЕНИЕМ, а не догадкой. Замер показал:
    #   commenter:algora-pbc     38 задач — почти все настоящие, платформа платит в USDC
    #   label:"💎 Bounty"       557 задач — та же платформа, но с примесью форков и песочниц
    #   label:bounty          4 019 задач — 95% мусор: боты-радары и security-программы
    #   "/attempt #"        407 831 задача — слово встречается в любом CI-логе, бесполезно
    # Поэтому первым идёт след платёжного бота, а широкие запросы — только хвостом.
    #
    # ЧТО ДОБАВЛЕНО И ПОЧЕМУ. Замер воронки показал неприятное: из шестидесяти
    # задач с GitHub до конца не доходила НИ ОДНА. Семь проходили первые
    # заслоны — и среди них «$50 Fix typo in README» и «$50 Add JSDoc», ровно
    # наш класс работы, — но все умирали на заслоне «не больше трёх
    # соперников»: у той площадки было сорок восемь и пятьдесят три
    # претендента.
    #
    # Отсюда вывод, который не следовал из общих соображений: искать надо не
    # «любые баунти», а те, что МЫ УМЕЕМ ЗАКРЫТЬ ДОКАЗУЕМО и куда не сбежалась
    # толпа. Документация и перевод — единственный класс, где мы можем
    # отчитаться построчной сверкой с исходниками, и именно его в списке
    # запросов не было вовсе.
    #
    # Свежесть тоже важна: задача разбирается за часы, и запрос без ограничения
    # по дате приносит то, что разобрали неделю назад.
    # ПОРЯДОК ЗАДАН ЗАМЕРОМ 12 СЕНТЯБРЯ, И ЗАМЕР ОПРОВЕРГ ПРЕЖНИЙ.
    #
    # Раньше первым шёл след платёжного бота: считалось, что там «почти все
    # находки настоящие». Проверка на живой выдаче показала обратное — из
    # тридцати его задач сумму удалось разобрать у ДВУХ. А метка «💵 Bounty»,
    # которую мы не искали ни разу, дала двадцать четыре из двадцати семи.
    #
    # Отдача каждого запроса (найдено -> из них с настоящей суммой):
    #   label:"💵 Bounty"                 27 -> 24   мы её не знали вовсе
    #   label:bounty + documentation      25 -> 16   НАШ класс работы
    #   "bounty" + "translation"          26 -> 10   НАШ класс работы
    #   "/bounty" python, свежие          30 ->  6
    #   label:"💎 Bounty"                 30 ->  5
    #   "/bounty" typescript, свежие      30 ->  3
    #   "/bounty" всё, с сентября         30 ->  3
    #   commenter:algora-pbc              30 ->  2   стоял ПЕРВЫМ
    #   label:bounty + i18n                2 ->  2   мало, но всё по делу
    #   label:"bounty 💰"                 10 ->  2
    #   "gitcoin" в теле                  10 ->  1
    #   label:"💰 Bounty"                 13 ->  0   выброшен
    #   "bounty" + label:documentation    17 ->  0   выброшен
    #   commenter:polar-sh                 1 ->  0   выброшен
    #   label:"help wanted" + docs         0 ->  0   выброшен
    #   algora-pbc + label:bug             1 ->  0   выброшен
    #
    # Порядок важен не из эстетики: список обрезается по пределу, и запрос,
    # стоящий последним, до обработки может не дожить. Раньше бюджет съедали
    # именно те, что давали ноль.
    queries = [
        'label:"💵 Bounty" state:open is:issue archived:false',
        'label:bounty state:open is:issue label:documentation archived:false',
        '"bounty" in:body state:open is:issue "translation" created:>2026-08-15',
        '"/bounty" in:body state:open is:issue language:python created:>2026-08-15',
        'label:"💎 Bounty" state:open is:issue archived:false',
        '"/bounty" in:body state:open is:issue language:typescript created:>2026-08-15',
        '"/bounty" in:body state:open is:issue created:>2026-09-01',
        'commenter:algora-pbc state:open is:issue',
        'label:bounty state:open is:issue label:i18n archived:false',
        'label:"bounty 💰" state:open is:issue archived:false',
        '"gitcoin" in:body state:open is:issue created:>2026-08-15',
    ]
    # КВОТА НА ЗАПРОС. Раньше строки складывались по порядку и резались на limit:
    # первые два запроса съедали всё, и запрос Opire (103 открытых задачи) давал
    # в очередь ноль. Теперь каждый запрос вносит не больше своей доли.
    per_query = max(8, limit // max(1, len(queries) // 2))
    seen, rows = set(), []
    for q in queries:
        out = _gh(["api", "-X", "GET", "search/issues", "-f", f"q={q}",
                   "-f", "per_page=30", "-f", "sort=created", "-f", "order=desc",
                   "--jq", ".items[] | {u:.html_url, t:.title, b:(.body//\"\"|.[0:900]), "
                           "n:.number, cm:.comments, "
                           "r:(.repository_url|split(\"/\")|.[-2:]|join(\"/\")), "
                           "l:[.labels[].name]} | @json"])
        taken = 0
        for line in (out or "").strip().split("\n"):
            if not line or taken >= per_query:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d["u"] in seen:
                continue
            seen.add(d["u"])
            if "opirebot" in q:
                d["l"] = list(d.get("l") or []) + ["opire"]    # источник награды — бот Opire
            rows.append(d)
            taken += 1
    return rows[:limit]


def enrich_and_score(rows):
    """Достаёт сумму, звёзды репозитория и считает пригодность."""
    out = []
    repo_cache = {}
    for d in rows:
        body = d.get("b", "")
        if not looks_like_real_money(body, d.get("l", [])):
            continue                      # песочница или своя валюта — мимо
        amount = parse_amount(d["t"] + " " + body)
        if not amount and "opire" in (d.get("l") or []):
            amount = _opire_reward(d["r"], d.get("n"))     # награда живёт в комментарии opirebot
        if not amount:
            continue
        repo = d["r"]

        # ═══ ЗАДАЧА ВНЕ GITHUB ═══
        # Здесь нет ни звёзд, ни заявок в обсуждении, зато есть разница,
        # которую нельзя замазать: КОНКУРС — ЭТО НЕ БАУНТИ. За баунти платят
        # тому, кто сделал работу; в конкурсе платят ОДНОМУ победителю, а
        # остальные работают бесплатно. Приз в миллион и баунти в миллион —
        # совершенно разные вещи, и складывать их в один список по величине
        # суммы значит обманывать себя самым приятным образом.
        #
        # Поэтому призовой фонд режется на порядок: он показывает масштаб
        # события, а не то, что мы можем получить.
        if "offsite" in (d.get("l") or []):
            is_contest = any(w in body.lower() for w in
                             ("приз", "prize", "соревнован", "contest", "competition"))
            expect = amount * (0.02 if is_contest else 0.6)
            from core import priority
            est = priority.bounty_estimate(amount, None, True, trust=0.5, stack_fit=0.5,
                                           rivals=0, comments=0, is_contest=is_contest,
                                           offsite=True)
            out.append({
                "url": d["u"], "repo": repo, "title": d["t"][:180],
                "amount": round(expect, 2), "stars": 0, "language": "",
                "rivals": 0,
                "payout": "неизвестно",
                "labels": ",".join(d.get("l", []))[:120],
                # формула GND §9, а не «ожидание × 0.5»
                "fit": priority.score(est),
                # НАГРАДУ ЗДЕСЬ ОБЪЯВИЛ САМ ОРГАНИЗАТОР. Признак ставится в этой
                # ветке, а не в общей: записи с площадок уходят отсюда с
                # continue и до общей проверки не доходили вовсе — поэтому у
                # всего найденного вне GitHub признак оставался невыясненным, и
                # заслон «работаем только за объявленное» отсекал ВСЁ.
                #
                # Отличие от GitHub существенно. Там сумму в заголовке мог
                # написать посторонний, и нужна метка проекта. Здесь призовой
                # фонд опубликован в каталоге самой площадки — постороннему
                # туда не вписать. Это объявление наградодателя, сделанное
                # публикацией, а не меткой.
                "declared": True,
                "declared_proof": f"призовой фонд опубликован площадкой {repo}",
                "note": (f"вне GitHub. Объявленная сумма ${amount:,.0f}"
                         + (", но это КОНКУРС: платят только победителю, "
                            "поэтому ожидание срезано в пятьдесят раз"
                            if is_contest else "")
                         + f". Приоритет: {priority.explain(est)}"),
            })
            continue

        if repo not in repo_cache:
            info = _gh(["api", f"repos/{repo}",
                        "--jq", "{s:.stargazers_count,l:(.language//\"\"),c:.created_at,f:.forks_count}"])
            try:
                repo_cache[repo] = json.loads(info) if info else {}
            except Exception:
                repo_cache[repo] = {}
        ri = repo_cache[repo]
        stars = ri.get("s", 0) or 0
        lang = (ri.get("l") or "").lower()

        # АГРЕГАТОР — НЕ ЗАДАЧА. «🎯 Micro Bounty Alert: 20 New Opportunities» из
        # репозитория BountyScout прошёл все фильтры: $25 дёшево рискнуть, метка
        # «bounty-alert» выглядела как метка проекта. Это пересказ чужих задач, и
        # заявка на него — шум, который при постоянном разрешении подавать заявки
        # агенты отправили бы сами.
        marks = " ".join([d["t"].lower(), " ".join(d.get("l") or []).lower(), repo.lower()])
        if any(k in marks for k in AGGREGATOR_HINTS):
            continue

        # отсев ферм: имя ради баунти, либо крупная сумма при малой известности
        low = repo.lower()
        if any(h in low for h in SPAM_HINTS) and stars < MIN_STARS_FOR_BIG:
            continue
        if amount >= BIG_AMOUNT and stars < MIN_STARS_FOR_BIG:
            continue          # $1250 от репозитория с 8 звёздами — почти всегда мусор

        # ПЛАТЁЖЕСПОСОБНОСТЬ, А НЕ ТОЛЬКО ОБЪЯВЛЕНИЕ. Проверка «награду объявил
        # проект» смотрит метку и права автора — и этого мало. Владелец
        # репозитория с ОДНОЙ звездой может повесить метку bounty на свою же
        # задачу, ничего не имея в виду и ничем не отвечая.
        #
        # Поймано на живой заявке: первая настоящая заявка ушла в репозиторий
        # cuentaprueba244w-dotcom — по-испански это дословно «тестовый аккаунт»,
        # одна звезда, заведён три месяца назад. Формально всё сходилось: метка
        # есть, задачу завёл владелец. По существу платить там некому.
        #
        # Признак платёжеспособности — либо известность репозитория, либо след
        # платёжной площадки в обсуждении. Без хотя бы одного из двух объявление
        # остаётся словами.
        # ЗАСЛОН СОРАЗМЕРЕН ТОМУ, ЧТО МЫ ТЕРЯЕМ.
        #
        # Одинаковое требование известности для любой суммы било мимо: мелкие
        # задачи на пять-тридцать долларов ЖИВУТ именно в маленьких репозиториях,
        # и требовать от них десяти звёзд значило отсечь весь этот слой целиком.
        # А владелец сказал прямо, что и пять долларов для нас годятся.
        #
        # Соразмерность считается по цене ошибки, а не по размеру награды. За
        # мелкую задачу мы теряем час работы исполнителя, и рискнуть можно. За
        # крупную теряем день и репутацию заявки — там нужна либо известность,
        # либо след платёжной площадки.
        cheap = amount <= SMALL_ENOUGH_TO_RISK
        backed = (cheap
                  or stars >= MIN_STARS_ANY
                  or any(k in body.lower() for k in
                         ("algora", "polar.sh", "gitcoin", "bountysource")))
        if not backed:
            continue

        # ДОЙДУТ ЛИ ДЕНЬГИ — проверяем ДО оценки работы
        pay = payout_reachable(repo, body)
        if pay is False:
            continue          # платят способом, недоступным владельцу

        # КТО НАЗНАЧИЛ СУММУ. Сумма в заголовке — ещё не награда: её мог
        # написать такой же соискатель. Мы на этом потеряли день, приняв за
        # работу на $25 задачу, где цену предложил посторонний, а мейнтейнеры
        # не ответили. Это не повод отбрасывать — это повод не врать себе
        # про ожидаемую выручку, поэтому вес задачи режется, а не обнуляется.
        # КТО ОБЪЯВИЛ НАГРАДУ — зависит от того, ГДЕ она объявлена.
        #
        # На GitHub сумма в заголовке ничего не значит: её мог написать такой
        # же соискатель, и мы на этом потеряли день. Там нужна метка проекта
        # или слово человека с правами.
        #
        # На площадке соревнований всё иначе: призовой фонд публикует САМ
        # ОРГАНИЗАТОР в своём же каталоге — постороннему туда не вписать. Это
        # и есть объявление наградодателя, просто сделанное не меткой, а
        # публикацией. Требовать от него метки GitHub бессмысленно: их там нет
        # по устройству площадки, и такое требование отсекало бы ВСЁ, что
        # найдено вне GitHub, — то есть почти всё найденное.
        # Сюда доходят только записи с GitHub: площадки ушли ранней веткой выше,
        # и признак им проставлен там. Дублировать условие здесь значило бы
        # завести вторую развилку по тому же признаку — они расходятся со
        # временем, и тогда одна половина кода считает иначе, чем другая.
        declared, proof = declared_by_project(repo, d.get("n"))

        # ЗАНЯТОСТЬ: сколько уже делают то же самое и не выплачено ли уже
        rivals, paid = competition(repo, d.get("n"))
        if paid:
            continue                      # премию уже получил другой — работать не за что
        if rivals >= 4:
            continue                      # задача фактически разобрана
        stack_fit = OUR_STACK.get(lang, 0.25)
        # доверие к репозиторию: звёзды сглаженно
        import math
        trust = min(1.0, math.log10(1 + stars) / 2.4)
        # чем больше соперников и обсуждения, тем ниже шанс, что возьмут именно нас
        crowd = 1.0 / (1 + rivals * 0.8 + (d.get("cm", 0) or 0) * 0.02)
        # неизвестный способ выплаты — не запрет, но и не повод вкладываться
        pay_factor = 1.0 if pay is True else 0.35
        # Необъявленная награда — самый тяжёлый множитель из всех. Работа по
        # ней не запрещена (вклад в открытый проект имеет смысл сам по себе),
        # но считать её выручкой нельзя, и в очередь она идёт последней.
        declared_factor = 1.0 if declared is True else (0.5 if declared is None else 0.08)
        # ПРИОРИТЕТ ПО ФОРМУЛЕ GND §9. Прежнее произведение уже содержало
        # почти все слагаемые, но не делило на срок до денег и на труд: задача
        # на $500 на две недели стояла выше задачи на $50 на вечер. Директива
        # требует обратного при прочих равных — небольшой задачи с быстрой
        # приёмкой. Валюта и сеть в расчёт не передаются вовсе.
        from core import priority
        est = priority.bounty_estimate(amount, pay, declared, trust, stack_fit,
                                       rivals, d.get("cm", 0) or 0)
        fit = priority.score(est)
        _ = (crowd, pay_factor, declared_factor)   # слагаемые перешли в priority
        out.append({"url": d["u"], "repo": repo, "title": d["t"][:180],
                    "amount": amount, "stars": stars, "language": lang, "rivals": rivals,
                    "payout": ("крипта/Algora" if pay is True else "неизвестно"),
                    "declared": declared,
                    "declared_proof": proof[:160],
                    "labels": ",".join(d.get("l", []))[:120], "fit": fit,
                    "note": f"приоритет: {priority.explain(est)}"})
    out.sort(key=lambda r: -r["fit"])
    # Не больше MAX_PER_REPO задач из одного репозитория: ферма иначе забьёт
    # весь список. Но АГРЕГАТОР — не ферма: mlcontests.com публикует чужие
    # независимые соревнования, у каждого свой организатор и свой призовой
    # фонд, и срезать его до двух позиций значит выбрасывать настоящую работу
    # тем же фильтром, что ловит подделки. Разница проверяемая: площадка
    # прошла проверку разведчика о четыре стены, репозиторий-ферма — нет.
    seen_repo, capped = {}, []
    for r in out:
        k = r["repo"]
        limit = MAX_PER_REPO_OFFSITE if "offsite" in (r.get("labels") or "") else MAX_PER_REPO
        seen_repo[k] = seen_repo.get(k, 0) + 1
        if seen_repo[k] <= limit:
            capped.append(r)
    return capped


# ════════════════════════════ ИСТОЧНИКИ ВНЕ GITHUB
#
# Владелец спросил прямо: «ты ищешь только на GitHub, а может есть где ещё».
# Вопрос был по делу. Разведчик заработка давно находил площадки за пределами
# GitHub — 62 штуки, 11 открытых, — но ОХОТНИК ЗА КОНКРЕТНЫМИ ЗАДАЧАМИ смотрел
# только в GitHub Search. Площадки были известны, а задачи с них не читались.
#
# Замер показал, какие из них отдают задачи машиночитаемо прямо в разметке:
#     mlcontests.com   89 разных сумм, до $1 048 576 — соревнования по данным
#     immunefi.com     14 сумм — премии за найденные уязвимости
#     replit.com       4 суммы — мелкие задачи на заказ
# А какие требуют браузера или аккаунта — те честно не включены, а не обойдены.
OFFSITE = [
    ("mlcontests.com", "https://mlcontests.com/",
     "соревнования по данным: призовой фонд объявлен заранее"),
    ("immunefi.com", "https://immunefi.com/bug-bounty/",
     "премии за найденные уязвимости, выплата в крипте"),
    # replit.com/bounties убран 14.09.2026: адрес редиректит на contra.com —
    # площадка закрыта. Источник, который не проверяют на жизнь, врёт молча.
]

# ЖИВА ЛИ ПЛОЩАДКА. Владелец поймал предложение мёртвого OnlyDust («Service
# discontinued. OnlyDust Has Closed»): страница отвечает 200, но это некролог.
# Признаки смерти — редирект на чужой домен или текст о закрытии; тишина сети
# — незнание (None), не смерть.
_DEAD_WORDS = ("service discontinued", "has closed", "is closed", "shut down", "shutting down",
               "no longer available", "sunset", "wind down", "winding down", "discontinued")


def platform_alive(url, timeout=20):
    """True — жива; False — мертва (редирект на чужой домен / текст о закрытии); None — не узнали."""
    import urllib.request, urllib.error
    from urllib.parse import urlparse
    try:
        r = urllib.request.urlopen(urllib.request.Request(
            url, headers={"User-Agent": "P0-agents/1.0 (+https://github.com/mike-lblc/project-zero)"}),
            timeout=timeout)
        final = urlparse(r.geturl()).netloc.lower().removeprefix("www.")
        want = urlparse(url).netloc.lower().removeprefix("www.")
        if final and final.split(".")[-2:] != want.split(".")[-2:]:
            return False                                  # уехали на чужой домен
        head = r.read(60000).decode("utf-8", "ignore").lower()
        head = re.sub(r"<script.*?</script>|<style.*?</style>", " ", head, flags=re.S)
        return not any(w in head for w in _DEAD_WORDS)
    except urllib.error.HTTPError as e:
        return False if e.code in (404, 410) else None
    except Exception:
        return None


_MONEY_RE = re.compile(r"\$\s?([\d,]{2,})(?!\d)")
_TITLE_RE = re.compile(r"<(?:h[1-4]|a)[^>]*>([^<]{12,90})</(?:h[1-4]|a)>", re.I)


def search_offsite(per_site=8):
    """Читает задачи с площадок ВНЕ GitHub.

    Возвращает те же записи, что и поиск по GitHub, чтобы они проходили ровно
    те же фильтры: настоящие ли деньги, дойдёт ли выплата, не разобрано ли.
    Иной источник не означает иных правил.
    """
    guard.check_action("research", "GREEN")
    import urllib.error
    import urllib.request
    # ЧЕСТНЫЙ ЗАГОЛОВОК. Здесь стоял заголовок браузера Chrome. Проверено 13.09.2026:
    # все источники отвечают честному роботу так же. Маскироваться под человека
    # запрещено правилами самих агентов — это обход проверки «человек или машина».
    ua = {"User-Agent": "P0-agents/1.0 (+https://github.com/mike-lblc/project-zero)"}
    rows = []
    for site, url, what in OFFSITE:
        alive = platform_alive(url)
        if alive is False:
            _API_TROUBLE.append(f"{site}: площадка мертва (редирект/закрытие)")
            bus.broadcast("bounty", f"Площадка {site} мертва (редирект на чужой домен или текст о "
                                    f"закрытии) — источник пропущен; предлагать её нельзя.")
            continue
        try:
            html = urllib.request.urlopen(
                urllib.request.Request(url, headers=ua), timeout=25
            ).read(300000).decode("utf-8", "ignore")
        except (urllib.error.URLError, OSError, ValueError) as e:
            _API_TROUBLE.append(f"{site}: {type(e).__name__}")
            continue

        # СНАЧАЛА ИЩЕМ СТРУКТУРУ, а не разбираем разметку глазами. mlcontests
        # кладёт весь список соревнований в атрибут data-competitions готовым
        # JSON: название, ссылка, приз, срок, площадка. Разбирать такое по
        # тегам — значит добровольно заменять факт догадкой.
        taken = 0
        for m in re.finditer(r'data-competitions="([^"]+)"', html):
            try:
                import html as _h
                items = json.loads(_h.unescape(m.group(1)))
            except (ValueError, TypeError):
                continue
            for it in items:
                if taken >= per_site:
                    break
                prize = str(it.get("prize") or "")
                amount = _MONEY_RE.search(prize)
                if not amount:
                    continue
                try:
                    val = float(amount.group(1).replace(",", ""))
                except ValueError:
                    continue
                if val < 20:
                    continue
                rows.append({
                    "u": it.get("url") or url,
                    "t": (it.get("name") or "")[:180],
                    "b": (f"{what}. Приз: {prize}. Срок: {it.get('deadline','?')}. "
                          f"Площадка: {it.get('platform','?')}. "
                          f"Метки: {', '.join(it.get('tags') or [])}"),
                    "n": None, "cm": 0, "r": site,
                    "l": ["offsite"] + list(it.get("tags") or [])})
                taken += 1

        if taken:
            continue          # структура нашлась, гадать по разметке не нужно

        # Разметка — запасной путь для площадок без структурированных данных.
        # Правило то же: нет суммы рядом с заголовком — задача не берётся.
        # Лучше пропустить, чем приписать чужую цену.
        text = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
        for m in _TITLE_RE.finditer(text):
            if taken >= per_site:
                break
            title = re.sub(r"\s+", " ", m.group(1)).strip()
            near = text[m.end():m.end() + 400]
            money = _MONEY_RE.search(near)
            if not money:
                continue
            try:
                amount = float(money.group(1).replace(",", ""))
            except ValueError:
                continue
            if amount < 20:
                continue
            rows.append({"u": url, "t": title[:180],
                         "b": f"{what}. Сумма со страницы: ${amount:,.0f}",
                         "n": None, "cm": 0, "r": site, "l": ["offsite"]})
            taken += 1
    return rows


# ДОСКА AIBTC — единственная найденная площадка, где ДРУГИЕ АГЕНТЫ И ЛЮДИ
# ПЛАТЯТ АГЕНТАМ В КРИПТЕ (sBTC на Stacks) по открытому API без скрейпинга:
# 56 задач, десятки со статусом paid, аудиты Clarity по 10–21k сатов. Найдена
# 14.09.2026 через пост другого агента в clawtasks на Moltbook. Сдача
# подписывается BTC-ключом зарегистрированного агента (L1), выплата — на
# STX-адрес: до регистрации владельцем задачи попадают в очередь как
# «найдено», а не «сдано».
AIBTC_API = "https://aibtc.com/api/bounties?status=open&limit=100"


def _flag_once(source, key, question, context):
    """Одна эскалация на (источник, ключ): повторные заходы молчат."""
    try:
        c = _con()
        c.execute("CREATE TABLE IF NOT EXISTS bounty_flagged (source TEXT, key TEXT, at TEXT, "
                  "PRIMARY KEY(source, key))")
        if c.execute("SELECT 1 FROM bounty_flagged WHERE source=? AND key=?", (source, key)).fetchone():
            c.close()
            return False
        c.execute("INSERT INTO bounty_flagged(source,key,at) VALUES (?,?,?)", (source, key, now()))
        c.commit(); c.close()
    except Exception:
        return False
    try:
        from agents import council
        council.escalate("bounty", question, json.dumps(context, ensure_ascii=False))
    except Exception:
        return False
    return True


def _btc_usd():
    """Курс для перевода сатов в доллары: публичный спот Coinbase, без ключа."""
    import urllib.request
    try:
        raw = urllib.request.urlopen(urllib.request.Request(
            "https://api.coinbase.com/v2/prices/BTC-USD/spot",
            headers={"User-Agent": "P0-agents/1.0"}), timeout=15).read()
        return float(json.loads(raw)["data"]["amount"])
    except Exception:
        return None


def search_aibtc():
    """Открытые задачи доски AIBTC в формате ingest(): url, title, amount_usd, note.

    Отсекаются задачи, где оплата — ставка (акции prediction-market), и задачи,
    требующие самим заплатить («make one paid query»): первое — не деньги,
    второе — трата, которая агентам запрещена.
    """
    import urllib.request
    try:
        raw = urllib.request.urlopen(urllib.request.Request(
            AIBTC_API, headers={"User-Agent": "P0-agents/1.0", "Accept": "application/json"}),
            timeout=25).read()
        data = json.loads(raw)
    except Exception as e:
        _API_TROUBLE.append(f"aibtc.com: {type(e).__name__}")
        return []
    items = data.get("bounties") or data.get("items") or data.get("data") or []
    price = _btc_usd()
    rows = []
    for b in items:
        desc = str(b.get("description") or "")
        low = desc.lower()
        if "paid in shares" in low or "reward is paid in shares" in low or "choosing to be paid in a bet" in low:
            continue                      # ставка, не деньги
        if "make one paid" in low or "from your own wallet" in low:
            continue                      # требует платить самим — запрещено
        try:
            sats = int(b.get("rewardSats") or 0)
        except (TypeError, ValueError):
            sats = 0
        if sats <= 0:
            continue
        usd = round(sats / 1e8 * price, 2) if price else round(sats / 1e8 * 60000, 2)
        # НАШ КЛАСС НА AIBTC — census/docs/cross-post/pitches/verify (150–5000 сатов,
        # в истории доски все они оплачены), а не аудиты Clarity. Такая задача
        # уходит в очередь суждений один раз, чтобы её взяли, пока она открыта.
        title_low = str(b.get("title") or "").lower()
        ours = (any(k in title_low or k in low[:600] for k in
                    ("census", "document", "docs", "cross-post", "pitch", "translate", "readme",
                     "summar", "postmortem", "write-up", "scout", "catalog", "list "))
                and not any(k in title_low for k in ("audit", "clarity", "exploit", "stress-test")))
        if ours:
            _flag_once("aibtc", str(b.get("id")),
                       f"AIBTC: задача НАШЕГО класса — «{title_low[:70]}» за {sats} сатов "
                       f"(сдач {b.get('submissionCount')}, до {str(b.get('expiresAt'))[:10]})",
                       {"url": f"https://aibtc.com/bounties/{b.get('id')}", "sats": sats,
                        "описание": desc[:1500]})
        rows.append({
            "url": f"https://aibtc.com/bounties/{b.get('id')}",
            "title": str(b.get("title") or "")[:180],
            "amount_usd": usd,
            "participants": int(b.get("submissionCount") or 0),
            "note": (f"AIBTC: {sats} сатов sBTC на Stacks; срок {str(b.get('expiresAt'))[:10]}; "
                     f"сдач {b.get('submissionCount')}; метки {', '.join(b.get('tags') or [])}. "
                     f"Сдача — подпись BTC-ключом зарегистрированного агента (L1), выплата на "
                     f"STX-адрес: нужна регистрация владельцем."),
            "declared_proof": f"объявлено площадкой aibtc.com: {sats} sats, "
                              f"poster {str(b.get('posterBtcAddress'))[:14]}…",
        })
    return rows


# DEWORK (app.dework.xyz) — доска задач DAO с наградами в криптотокенах. GraphQL-API
# публичный (без входа): getPaginatedTasks(filter: SearchTasksInput). Заявка и
# выплата — через аккаунт владельца (вход кошельком; вход через GitHub у них
# ломается собственным CSP). Интроспекция закрыта — форма запроса снята с их
# фронтенда 14.09.2026.
DEWORK_GQL = "https://api.dework.xyz/graphql"
_DEWORK_Q = ("query P($filter: SearchTasksInput!, $cursor: String) { paginated: getPaginatedTasks("
             "filter: $filter, cursor: $cursor) { total cursor tasks { id name status dueDate createdAt "
             "rewards { amount peggedToUsd token { symbol %s network { slug } } } "
             "workspace { slug organization { name slug } } applications { id } } } }")
_STABLE = {"USDC": 6, "USDT": 6, "USDC.E": 6, "DAI": 18, "USDBC": 6, "BUSD": 18}
# Десятичность известных токенов (у PaymentToken в API поля decimals нет): без неё
# 500000000 wei читалось как 5e-10 ETH, а 1 SOL — как 1e-9.
_DECIMALS = {**_STABLE, "ETH": 18, "WETH": 18, "MATIC": 18, "POL": 18, "SOL": 9, "BTC": 8, "WBTC": 8}


def search_dework(pages=4):
    """Задачи Dework с ненулевой наградой в формате ingest()."""
    import urllib.request
    rows, cursor = [], None
    query = _DEWORK_Q % ""          # у PaymentToken нет поля decimals — десятичность по таблице стейблов
    for _ in range(pages):
        body = json.dumps({"query": query, "variables": {
            "filter": {"statuses": ["TODO"], "sortBy": {"field": "createdAt", "direction": "DESC"}},
            "cursor": cursor}}).encode()
        try:
            raw = urllib.request.urlopen(urllib.request.Request(
                DEWORK_GQL, data=body, headers={"Content-Type": "application/json",
                                                "User-Agent": "P0-agents/1.0"}), timeout=30).read()
            data = json.loads(raw)
        except Exception as e:
            _API_TROUBLE.append(f"dework: {type(e).__name__}")
            break
        if data.get("errors"):
            _API_TROUBLE.append("dework: graphql errors")
            break
        page = (data.get("data") or {}).get("paginated") or {}
        for t in page.get("tasks") or []:
            rewards = [r for r in (t.get("rewards") or []) if r.get("amount")]
            if not rewards:
                continue
            ws = t.get("workspace") or {}
            org = (ws.get("organization") or {})
            usd, parts = 0.0, []
            for r in rewards:
                tok = r.get("token") or {}
                sym = str(tok.get("symbol") or "?")
                dec = _DECIMALS.get(sym.upper(), 18)
                try:
                    amt = int(r["amount"]) / (10 ** int(dec))
                except (TypeError, ValueError):
                    continue
                parts.append(f"{amt:g} {sym} ({(tok.get('network') or {}).get('slug')})")
                if sym.upper() in _STABLE or r.get("peggedToUsd"):
                    usd += amt
            url = (f"https://app.dework.xyz/o/{org.get('slug')}/p/{ws.get('slug')}?taskId={t['id']}"
                   if org.get("slug") and ws.get("slug") else f"https://app.dework.xyz/task/{t['id']}")
            rows.append({
                "url": url, "title": f"[{org.get('name') or 'DAO'}] {t.get('name') or ''}"[:180],
                "amount_usd": round(usd, 2),
                "participants": len(t.get("applications") or []),
                "note": (f"Dework: награда {', '.join(parts)}; заявок {len(t.get('applications') or [])}; "
                         f"срок {str(t.get('dueDate') or '-')[:10]}. Заявка и выплата — через аккаунт "
                         f"владельца на Dework (вход кошельком)."),
                "declared_proof": f"объявлено DAO на Dework: {', '.join(parts)}",
            })
        cursor = page.get("cursor")
        if not cursor:
            break
    return rows


# OPIRE (opire.dev) — награды на issue GitHub. Публичный список: api.opire.dev/rewards
# (страницы ?page=N по 30). Поиск GitHub по боту opirebot давал спам-форки с
# «награды нет», поэтому источник — только API. ОСТОРОЖНО С СУММОЙ: pendingPrice —
# сумма ОБЕЩАНИЙ любых пользователей без эскроу (у GmsCore «$1,9 млн» при заявленных
# в заголовке $14 999) — берём не больше суммы из заголовка, если она там есть.
# Поток: /try в issue → PR → /claim #N в PR; платит создатель награды после проверки;
# рельс выплат Opire — Stripe (фиат) → при /claim просить USDC напрямую.
OPIRE_API = "https://api.opire.dev/rewards"


def search_opire(pages=3):
    """Открытые награды Opire в формате ingest(): url, title, amount_usd, participants, note."""
    import urllib.request
    rows = []
    for page in range(1, pages + 1):
        url = OPIRE_API + (f"?page={page}" if page > 1 else "")
        try:
            raw = urllib.request.urlopen(urllib.request.Request(
                url, headers={"User-Agent": "P0-agents/1.0", "Accept": "application/json"}),
                timeout=30).read()
            items = json.loads(raw)
        except Exception as e:
            _API_TROUBLE.append(f"opire.dev: {type(e).__name__}")
            break
        if not isinstance(items, list) or not items:
            break
        for it in items:
            price = it.get("pendingPrice") or {}
            try:
                pending = float(price.get("value") or 0) / (100.0 if price.get("unit") == "USD_CENT" else 1.0)
            except (TypeError, ValueError):
                pending = 0.0
            title = str(it.get("title") or "")
            declared = parse_amount(title)          # сумма, названная в заголовке проектом
            if not declared:                         # «[14999$]» — доллар после числа
                m = re.search(r"(\d[\d,]{0,6})\s?\$", title)
                if m:
                    try:
                        v = float(m.group(1).replace(",", ""))
                        declared = v if 5 <= v <= 50000 else None
                    except ValueError:
                        declared = None
            # Без суммы в заголовке доверяем ожиданию только до $5 000: выше — почти
            # всегда обещания ботов без эскроу (14.09: «$1,36 млн» у пустого репо).
            amount = min(pending, declared) if declared else (pending if pending <= 5000 else None)
            if not amount or amount < 5:
                continue
            issue_url = str(it.get("url") or "")
            if "github.com" not in issue_url:
                continue
            claimers = len(it.get("claimerUsers") or [])
            triers = len(it.get("tryingUsers") or [])
            org = (it.get("organization") or {}).get("name") or ""
            proj = (it.get("project") or {}).get("name") or ""
            langs = ", ".join(it.get("programmingLanguages") or [])[:60]
            rows.append({
                "url": issue_url,
                "title": f"[{org}/{proj}] {title}"[:180],
                "amount_usd": round(amount, 2),
                "participants": max(claimers, triers),
                "note": (f"Opire: в ожидании ${pending:,.0f} (обещания без эскроу"
                         + (f"; в заголовке ${declared:,.0f}" if declared else "") + f"); "
                         f"заявили /try {triers}, /claim {claimers}; языки {langs or '-'}. "
                         f"Поток: /try → PR → /claim #N; платит создатель награды; рельс Opire — "
                         f"Stripe (фиат) → просить USDC напрямую."),
                "declared_proof": f"награда опубликована площадкой opire.dev: ${amount:,.0f}",
            })
        if len(items) < 30:
            break
    return rows


# Конкурс опознаётся по источнику и пометке, а не по сумме.
CONTEST_SQL = ("repo LIKE '%devpost%' OR repo LIKE '%mlcontests%' "
               "OR note LIKE '%конкурс%' OR note LIKE '%КОНКУРС%'")


def rescore(c):
    """Пересчитывает приоритет у всего, что лежит в очереди, по формуле GND §9.

    Без этого старые записи сохраняли прежний приоритет, пока их источник не
    отзовётся: конкурс с призом $740 000 держал «приз × 0,02 = 14 800» и стоял
    выше любой настоящей задачи, потому что Devpost в тот час не отвечал.
    """
    import math
    from core import priority
    rows = c.execute(f"SELECT id, amount_usd, stars, language, rivals, payout, declared, "
                     f"({CONTEST_SQL}), prizes FROM bounties WHERE status='found'").fetchall()
    for bid, amount, stars, lang, rivals, payout, declared, contest, prizes in rows:
        reach = True if (payout or "").startswith("крипта") else None
        dec = None if declared is None else bool(declared)
        if contest:
            e = priority.bounty_estimate(amount or 0, reach, True, 0.5, 0.5, 0, 0,
                                         is_contest=True, offsite=True,
                                         participants=rivals or None, prizes=prizes)
        else:
            trust = min(1.0, math.log10(1 + (stars or 0)) / 2.4)
            e = priority.bounty_estimate(amount or 0, reach, dec, trust,
                                         OUR_STACK.get((lang or "").lower(), 0.25),
                                         rivals or 0, 0)
        c.execute("UPDATE bounties SET fit_score=? WHERE id=?", (priority.score(e), bid))
    c.commit()
    return len(rows)


def ingest(rows, source, declared=True, note=""):
    """Кладёт находки стороннего источника в общий конвейер работы.

    ЗАЧЕМ ЭТО ПОЯВИЛОСЬ. Мы подключили Devpost и Hacker News, написали к ним
    инструменты — и эти инструменты ВОЗВРАЩАЛИ СТРОКУ В ЧАТ. Девять открытых
    конкурсов находились каждый цикл и выбрасывались, потому что попасть в
    таблицу работы им было неоткуда. Со стороны это выглядело ровно так, как
    сказал владелец: поиск зациклен и показывает одни и те же пять задач.

    Источник, чьи находки никуда не записываются, — это не источник, а
    упражнение. Разница между «мы это видим» и «система с этим работает»
    проходит здесь, в одной функции.

    rows — список словарей: url, title, amount_usd, и по желанию note.
    Возвращает (сколько новых, сколько подтверждено заново).
    """
    if not rows:
        return 0, 0
    c = _con()
    new = seen = 0
    for r in rows:
        url = r.get("url")
        if not url:
            continue
        exists = c.execute("SELECT id FROM bounties WHERE url=?", (url,)).fetchone()
        amount = float(r.get("amount_usd") or 0)
        from core import priority
        contest = "конкурс" in ((note or "") + (r.get("note") or "")).lower()
        participants = r.get("participants")
        prizes = r.get("prizes")
        est = priority.bounty_estimate(amount, None, True if declared else None,
                                       trust=0.5, stack_fit=0.5, rivals=0, comments=0,
                                       is_contest=contest, offsite=True,
                                       participants=participants, prizes=prizes)
        # участники конкурса и есть соперники: пишем их в rivals
        payload = (url, source, str(r.get("title") or "")[:180], amount, "USD",
                   0, "", source, priority.score(est), int(participants or 0), "неизвестно",
                   (note or r.get("note") or "")[:300], now(),
                   1 if declared else 0,
                   r.get("declared_proof") or f"опубликовано площадкой {source}")
        try:
            c.execute("""INSERT INTO bounties(url,repo,title,amount_usd,currency,stars,
                         language,labels,fit_score,rivals,payout,note,found_at,
                         declared,declared_proof)
                         VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                         ON CONFLICT(url) DO UPDATE SET
                           amount_usd=excluded.amount_usd,
                           fit_score=excluded.fit_score,
                           rivals=excluded.rivals,
                           note=excluded.note,
                           status='found',
                           last_seen=excluded.found_at""", payload)
            if prizes:
                c.execute("UPDATE bounties SET prizes=? WHERE url=?", (int(prizes), url))
            new += 0 if exists else 1
            seen += 1 if exists else 0
        except Exception:
            continue
    c.commit(); c.close()
    return new, seen


def hunt(limit=60):
    """Полный заход: найти, оценить, записать."""
    _API_TROUBLE.clear()
    # GitHub плюс площадки вне его. Один источник — это одна слепая зона.
    raw = search(limit) + search_offsite()
    rows = enrich_and_score(raw)
    # Отказ API — единственный случай, когда заход НЕЛЬЗЯ считать проведённым:
    # мы не видели рынок, значит и снимать задачи с очереди не имеем права.
    if _API_TROUBLE and not rows:
        why = ", ".join(sorted(set(_API_TROUBLE))[:3])
        bus.broadcast("bounty", f"Заход НЕ СОСТОЯЛСЯ: API отказал ({why}). "
                                f"Это не «работы нет» — это «я не смог посмотреть». "
                                f"Повторю в следующем цикле.")
        return f"поиск не выполнен: {why}"
    # Доска AIBTC — ДО открытия соединения ниже: ingest пишет сам, и две
    # незакрытые записи в одной базе ловят «database is locked».
    try:
        new_ab, seen_ab = ingest(search_aibtc(), "aibtc.com",
                                 note="AIBTC: задачи за sBTC, сдача подписью BTC-ключа (L1)")
        if new_ab:
            bus.broadcast("bounty", f"AIBTC: новых задач за sBTC — {new_ab} (в очереди как «найдено»; "
                                    f"сдача возможна после регистрации агента владельцем).")
    except Exception as e:
        _API_TROUBLE.append(f"aibtc.com: {type(e).__name__}")
    try:
        new_op, _ = ingest(search_opire(), "opire.dev",
                           note="Opire: награды на issue GitHub (публичный API); /try → PR → /claim #N")
        if new_op:
            bus.broadcast("bounty", f"Opire: новых наград — {new_op} (владелец подключил GitHub к Opire; "
                                    f"сдача — /try, PR, /claim #N; выплату просить в USDC).")
    except Exception as e:
        _API_TROUBLE.append(f"opire.dev: {type(e).__name__}")
    try:
        new_dw, _ = ingest(search_dework(), "dework.xyz",
                           note="Dework: задачи DAO с наградой в криптотокенах (публичный GraphQL)")
        if new_dw:
            bus.broadcast("bounty", f"Dework: новых задач с наградой — {new_dw} (в очереди как «найдено»).")
    except Exception as e:
        _API_TROUBLE.append(f"dework: {type(e).__name__}")
    c = _con()
    for r in rows:
        c.execute("""INSERT INTO bounties(url,repo,title,amount_usd,currency,stars,language,
                     labels,fit_score,rivals,payout,note,found_at,declared,declared_proof)
                     VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                     ON CONFLICT(url) DO UPDATE SET amount_usd=?, fit_score=?, stars=?,
                     rivals=?, status='found', note=excluded.note, declared=excluded.declared,
                     declared_proof=excluded.declared_proof""",
                  (r["url"], r["repo"], r["title"], r["amount"], "USD", r["stars"],
                   r["language"], r["labels"], r["fit"], r.get("rivals", 0),
                   r.get("payout"), r.get("note"), now(),
                   None if r.get("declared") is None else (1 if r["declared"] else 0),
                   r.get("declared_proof"),
                   r["amount"], r["fit"], r["stars"], r.get("rivals", 0)))
    # ЧИСТКА. Задача, не прошедшая фильтры в этот заход, больше не «доступная»:
    # премию могли выплатить, толпа могла набежать. Оставлять её в очереди —
    # значит однажды вложить работу в разобранное. Поймано на tscircuit#92:
    # 72 заявки и уже выплаченные $75, а в базе висело «соперников 0».
    alive = {r["url"] for r in rows}
    # СМЕТАЕМ ТОЛЬКО ТО, ЧТО САМИ И ПРОВЕРЯЛИ.
    #
    # Здесь была тихая потеря, объяснявшая жалобу владельца «поиск зациклен и
    # показывает одни и те же пять задач». Заход помечал «потерянным» ВСЁ, чего
    # не увидел сам, — включая находки других источников. Девять конкурсов с
    # Devpost заходили в конвейер и стирались следующим же заходом охотника,
    # который про Devpost ничего не знает.
    #
    # Отсутствие в МОЕЙ выдаче не означает исчезновения задачи: оно означает,
    # что я туда не смотрел. Это ровно то различие между молчанием и пустотой,
    # которое мы соблюдаем везде, — и не соблюдали здесь.
    OWN = ("github.com",)          # источники, которые этот заход действительно обходит
    stale = c.execute("SELECT url,repo FROM bounties WHERE status='found'").fetchall()
    dropped = 0
    for url, repo in stale:
        if not any(d in (url or "") for d in OWN):
            continue               # чужой источник — не мне решать, жива ли она
        if url not in alive:
            c.execute("UPDATE bounties SET status='lost', note=? WHERE url=?",
                      ("не прошла повторную проверку: разобрана, выплачена или недостижима", url))
            dropped += 1
    c.commit()
    rescore(c)
    tot = c.execute("SELECT COUNT(*) FROM bounties WHERE status='found'").fetchone()[0]
    # ПРИЗОВОЙ ФОНД — НЕ ДОСТУПНЫЕ ДЕНЬГИ. Сводка складывала премии за задачи
    # с призовыми фондами конкурсов и докладывала «доступно 17 на $1 293 302»:
    # три задачи на $67 и четырнадцать конкурсов, где платят одному победителю.
    money = c.execute("SELECT COALESCE(SUM(amount_usd),0) FROM bounties WHERE status='found' "
                      f"AND NOT ({CONTEST_SQL})").fetchone()[0]
    n_contests, pools = c.execute("SELECT COUNT(*), COALESCE(SUM(amount_usd),0) FROM bounties "
                                  f"WHERE status='found' AND ({CONTEST_SQL})").fetchone()
    top = c.execute("SELECT title,amount_usd,repo FROM bounties WHERE status='found' "
                    "ORDER BY fit_score DESC, declared DESC, amount_usd ASC LIMIT 1").fetchone()
    c.close()
    if not tot:
        bus.broadcast("bounty", f"Просмотрел {len(raw)} открытых задач: доступных не осталось, "
                                f"{dropped} снято с очереди как разобранные или уже "
                                f"выплаченные. Рынок баунти забит конкурирующими агентами — "
                                f"нужен менее людный источник работы.")
        return f"просмотрено {len(raw)}, доступных нет, снято {dropped}"
    contests = (f"; конкурсов {n_contests} с призовыми фондами ${pools:,.0f} — "
                f"платят одному победителю, это не доступные деньги" if n_contests else "")
    bus.broadcast("bounty", f"Доступных задач с наградой: {tot - n_contests} на ${money:.0f}"
                            + contests
                            + (f", снято с очереди {dropped}" if dropped else "")
                            + f". Первая по приоритету: {top[2]} — ${top[1]:.0f}.")
    return (f"задач с наградой {tot - n_contests} на ${money:.0f}{contests}, снято {dropped}")


def shortlist(n=10):
    c = _con()
    rows = c.execute("""SELECT url,repo,title,amount_usd,stars,language,fit_score
                        FROM bounties WHERE status='found'
                        ORDER BY fit_score DESC, declared DESC, amount_usd ASC LIMIT ?""", (n,)).fetchall()
    c.close()
    return [dict(zip(("url", "repo", "title", "amount", "stars", "language", "fit"), r))
            for r in rows]


def fresh_bounties(max_age_hours=6, max_rivals=3):
    """Перехват СВЕЖИХ премий — пока на них не набежала толпа.

    Почему это отдельный инструмент, а не настройка охотника. Замер живого
    рынка показал жёсткую картину: на задаче tscircuit#92 за $75 висит
    72 заявки и два десятка присланных PR, и премия уже выплачена. Полный
    обход рынка вернул НОЛЬ доступных задач из шестидесяти просмотренных.
    Рынок не пустой — он разбирается за часы конкурирующими агентами.

    Значит единственное доступное нам преимущество — не качество разбора
    и не размер суммы, а СКОРОСТЬ. Задача, которой шесть часов и на которой
    ещё нет трёх заявок, — единственная, где у нас есть шанс быть первыми.

    Проверка идёт часто и стоит один запрос поиска: за квоту можно не бояться.
    """
    guard.check_action("research", "GREEN")
    _API_TROUBLE.clear()
    since = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    q = f"commenter:algora-pbc state:open is:issue created:>{since.strftime('%Y-%m-%dT%H:%M:%SZ')}"
    out = _gh(["api", "-X", "GET", "search/issues", "-f", f"q={q}",
               "-f", "per_page=25", "-f", "sort=created", "-f", "order=desc",
               "--jq", '.items[] | {u:.html_url, t:.title, b:(.body//""|.[0:900]), '
                       'n:.number, cm:.comments, created:.created_at, '
                       'r:(.repository_url|split("/")|.[-2:]|join("/")), '
                       'l:[.labels[].name]} | @json'])
    if not out:
        if _API_TROUBLE:
            return f"перехват не выполнен: {', '.join(sorted(set(_API_TROUBLE))[:2])}"
        return f"свежих премий за {max_age_hours} ч нет"

    hot = []
    for line in out.strip().split("\n"):
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not looks_like_real_money(d.get("b", ""), d.get("l", [])):
            continue
        amount = parse_amount(d["t"] + " " + d.get("b", ""))
        if not amount:
            continue
        rivals, paid = competition(d["r"], d["n"])
        if paid or rivals > max_rivals:
            continue
        hot.append({"url": d["u"], "repo": d["r"], "title": d["t"][:180],
                    "amount": amount, "rivals": rivals, "created": d.get("created")})

    if not hot:
        return f"свежих премий за {max_age_hours} ч нет (проверено, толпа везде)"

    hot.sort(key=lambda h: (h["rivals"], -h["amount"]))
    from core import events
    for h in hot[:3]:
        events.publish("fresh_bounty", h, source="bounty")
    c = _con()
    for h in hot:
        c.execute("""INSERT INTO bounties(url,repo,title,amount_usd,currency,rivals,
                     status,note,found_at) VALUES (?,?,?,?,?,?,'found',?,?)
                     ON CONFLICT(url) DO UPDATE SET rivals=?, status='found'""",
                  (h["url"], h["repo"], h["title"], h["amount"], "USD", h["rivals"],
                   f"перехвачена свежей: возраст до {max_age_hours} ч", now(), h["rivals"]))
    c.commit(); c.close()
    top = hot[0]
    bus.broadcast("bounty", f"СВЕЖАЯ ПРЕМИЯ, толпы ещё нет: {top['repo']} за ${top['amount']:.0f}, "
                            f"заявок {top['rivals']}. {top['title'][:70]}. "
                            f"Здесь решает скорость — берём сейчас или не берём вовсе.")
    return f"перехвачено свежих: {len(hot)}, лучшая ${top['amount']:.0f} при {top['rivals']} заявках"


CYCLE = [("hunt_bounties", lambda: hunt(40))]
FAST_CYCLE = [("fresh_bounties", lambda: fresh_bounties(6, 3))]


if __name__ == "__main__":
    print(hunt())
    print()
    print("═══ ЛУЧШИЕ ПО ПРИГОДНОСТИ ═══")
    for b in shortlist(12):
        print(f"  ${b['amount']:>7.0f}  оценка {b['fit']:>7.1f}  {b['language'][:10]:10} "
              f"звёзд {b['stars']:>5}  {b['repo'][:28]:28}")
        print(f"           {b['title'][:88]}")
