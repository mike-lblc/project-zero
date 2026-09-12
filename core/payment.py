"""МАРШРУТИЗАТОР ПЛАТЕЖЕЙ — деньги приходят многими путями, а не одним.

Директива требует снять установку, по которой Base/USDC является основной
сетью. Требование не косметическое: до сих пор охотник отбраковывал работу,
платящую иначе, ЕЩЁ ДО ОЦЕНКИ. Мы сужали рынок на входе — не потому, что
выплата была недоступна, а потому, что у нас был написан один адаптер.

ЧЕГО ЭТОТ МОДУЛЬ НЕ ДЕЛАЕТ И НЕ БУДЕТ. Он не перемещает средства, не обменивает
их и не торгует. Ни одной функции, способной распорядиться деньгами, здесь нет
по построению: он только описывает, КУДА можно получить, проверяет пригодность
маршрута и сверяет поступление. Право получать и право распоряжаться — разные
права, и второго у системы нет.

ШЕСТЬ СОСТОЯНИЙ ДЕНЕГ, КОТОРЫЕ НЕЛЬЗЯ ПУТАТЬ. Директива перечисляет их прямо,
и каждое смешение однажды выдавало желаемое за полученное:

    обещано        кто-то сказал, что заплатит
    выиграно       награда присуждена нам
    начислено      сумма видна в личном кабинете площадки
    выводимо       её действительно можно забрать
    получено       она пришла на наш адрес или счёт
    выведено       она у владельца в месте, которым он распоряжается

Обещание, счёт, ожидающая выплата и неподтверждённая награда прибылью не
являются. HTTP 402 не является платежом: это приглашение заплатить.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.db import connect, ensure_schema, write  # noqa: E402

SCHEMA = """
CREATE TABLE IF NOT EXISTS payment_routes (
  id INTEGER PRIMARY KEY,
  provider TEXT NOT NULL,
  method TEXT NOT NULL,
  currency TEXT,
  -- ПУСТАЯ СТРОКА, А НЕ NULL. В SQL NULL не равен NULL, поэтому ограничение
  -- уникальности на маршрутах без сети не работало вовсе: каждый посев добавлял
  -- одиннадцать новых копий тех же строк, и реестр рос сам по себе. Тихая порча:
  -- список выглядит богаче, чем есть, и решения принимаются по раздутому числу.
  network TEXT NOT NULL DEFAULT '',
  destination TEXT,
  country_eligibility TEXT,
  kyc_required INTEGER,
  account_ready INTEGER,
  minimum_payout REAL,
  estimated_fee TEXT,
  settlement_time TEXT,
  withdrawal_available INTEGER,
  verified_at TEXT,
  status TEXT NOT NULL DEFAULT 'unverified',
  failure_reason TEXT,
  UNIQUE(provider, method, currency, network)
);
CREATE TABLE IF NOT EXISTS payment_requests (
  id INTEGER PRIMARY KEY,
  route_id INTEGER,
  opportunity TEXT,
  amount REAL,
  currency TEXT,
  instructions TEXT,
  requested_at TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'PAYMENT_REQUESTED'
);
CREATE TABLE IF NOT EXISTS payment_receipts (
  id INTEGER PRIMARY KEY,
  request_id INTEGER,
  route_id INTEGER,
  gross REAL,
  fees REAL,
  net REAL,
  currency TEXT,
  proof TEXT NOT NULL,
  proof_kind TEXT NOT NULL,
  from_party TEXT,
  received_at TEXT NOT NULL,
  UNIQUE(proof)
);
CREATE TABLE IF NOT EXISTS platform_balances (
  id INTEGER PRIMARY KEY,
  provider TEXT NOT NULL,
  currency TEXT,
  amount REAL,
  withdrawable INTEGER,
  checked_at TEXT NOT NULL,
  note TEXT,
  UNIQUE(provider, currency)
);
CREATE TABLE IF NOT EXISTS withdrawal_status (
  id INTEGER PRIMARY KEY,
  provider TEXT NOT NULL,
  amount REAL,
  currency TEXT,
  state TEXT NOT NULL,
  proof TEXT,
  at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS currencies (
  code TEXT PRIMARY KEY,
  kind TEXT,
  liquid INTEGER,
  note TEXT
);
CREATE TABLE IF NOT EXISTS networks (
  name TEXT PRIMARY KEY,
  chain_id TEXT,
  native_asset TEXT,
  needs_gas INTEGER,
  note TEXT
);
CREATE TABLE IF NOT EXISTS payout_requirements (
  id INTEGER PRIMARY KEY,
  provider TEXT NOT NULL,
  requirement TEXT NOT NULL,
  satisfied INTEGER,
  blocker TEXT,
  checked_at TEXT,
  UNIQUE(provider, requirement)
);
"""

# Состояния денег. Порядок значим: каждое следующее сильнее предыдущего, и
# перепрыгивать через них нельзя — именно так обещание превращалось в отчёт
# о прибыли.
MONEY_STATES = ("PROMISED", "WON", "CREDITED", "WITHDRAWABLE", "RECEIVED", "WITHDRAWN")

# Доказательства, которые считаются настоящими. Всё прочее — слова.
PROOF_KINDS = {
    "tx_hash": "хеш транзакции в публичной сети",
    "provider_receipt": "квитанция платёжного провайдера с номером",
    "bank_reference": "банковская ссылка перевода",
    "platform_payout_id": "идентификатор выплаты площадки",
}


def now():
    return datetime.now(timezone.utc).isoformat()


def _con():
    c = connect()
    ensure_schema(c, SCHEMA)
    return c


# ═════════════════════════════════════════════ АДАПТЕРЫ ВЛАДЕЛЬЦА
# Адреса, которыми владелец действительно распоряжается. Наличие адреса НЕ
# означает, что любая сеть поддерживается: для каждой проверяются отдельно
# актив, идентификатор сети, контракт токена, минимальная выплата, комиссии,
# нужен ли газ и можно ли вывести. Директива требует этого прямо.
OWNER_DESTINATIONS = {
    "evm": "0xECa891e34b3E5873181Fb779672564E198C55354",
    "btc": "bc1qqwgyyqv6raq2jnghals2n2aujgwd4e9p64g4hr",
}

# Посев маршрутов. Ни один не объявляется рабочим заранее: статус
# «unverified», пока проверка не подтвердит. Объявить маршрут рабочим без
# проверки — это ровно то обещание, которое директива запрещает считать
# прибылью.
SEED_ROUTES = [
    ("self-custody", "crypto", "USDC", "base", OWNER_DESTINATIONS["evm"]),
    ("self-custody", "crypto", "USDC", "ethereum", OWNER_DESTINATIONS["evm"]),
    ("self-custody", "crypto", "USDC", "polygon", OWNER_DESTINATIONS["evm"]),
    ("self-custody", "crypto", "USDC", "arbitrum", OWNER_DESTINATIONS["evm"]),
    ("self-custody", "crypto", "ETH", "ethereum", OWNER_DESTINATIONS["evm"]),
    ("self-custody", "crypto", "ETH", "base", OWNER_DESTINATIONS["evm"]),
    ("self-custody", "crypto", "BTC", "bitcoin", OWNER_DESTINATIONS["btc"]),
    ("algora", "bounty_platform", "USD", "", None),
    ("polar", "bounty_platform", "USD", "", None),
    ("gitcoin", "bounty_platform", "USD", "", None),
    ("github_sponsors", "sponsorship", "USD", "", None),
    ("ko-fi", "donation", "USD", "", None),
    ("open_collective", "grant", "USD", "", None),
    ("bank_transfer", "bank", "USD", "", None),
    ("payment_processor", "processor", "USD", "", None),
    ("marketplace_payout", "marketplace", "USD", "", None),
    ("affiliate_network", "affiliate", "USD", "", None),
    ("contest_prize", "prize", "USD", "", None),
]

SEED_NETWORKS = [
    ("base", "eip155:8453", "ETH", 1, "USDC 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"),
    ("ethereum", "eip155:1", "ETH", 1, "комиссии выше прочих сетей"),
    ("polygon", "eip155:137", "POL", 1, None),
    ("arbitrum", "eip155:42161", "ETH", 1, None),
    ("bitcoin", "bitcoin", "BTC", 1, "адрес bech32, сегвит"),
]

SEED_CURRENCIES = [
    ("USDC", "stablecoin", 1, None),
    ("ETH", "crypto", 1, None),
    ("BTC", "crypto", 1, None),
    ("USD", "fiat", 1, "через площадку или банк"),
    ("giftcard", "voucher", 0,
     "засчитывается ТОЛЬКО если законна, передаваема и полезна владельцу"),
]


def seed():
    """Заводит маршруты, сети и валюты. Все — непроверенными."""
    c = _con()
    added = 0
    for provider, method, currency, network, dest in SEED_ROUTES:
        cur = c.execute(
            "INSERT INTO payment_routes(provider,method,currency,network,destination,status) "
            "VALUES (?,?,?,?,?,'unverified') "
            "ON CONFLICT(provider,method,currency,network) DO NOTHING",
            (provider, method, currency, network, dest))
        added += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
    for name, chain, asset, gas, note in SEED_NETWORKS:
        c.execute("INSERT INTO networks(name,chain_id,native_asset,needs_gas,note) "
                  "VALUES (?,?,?,?,?) ON CONFLICT(name) DO NOTHING",
                  (name, chain, asset, gas, note))
    for code, kind, liquid, note in SEED_CURRENCIES:
        c.execute("INSERT INTO currencies(code,kind,liquid,note) VALUES (?,?,?,?) "
                  "ON CONFLICT(code) DO NOTHING", (code, kind, liquid, note))
    c.commit(); c.close()
    return added


# ═════════════════════════════════════════════ ПРИГОДНОСТЬ МАРШРУТА
def reachable(currency=None, network=None, provider=None):
    """Можно ли получить оплату этим способом. Возвращает (да/нет/неизвестно, почему).

    Три ответа, а не два. «Неизвестно» — законный результат: маршрут, который
    мы ещё не проверяли, нельзя ни использовать, ни отбрасывать. Директива
    требует отклонять работу ТОЛЬКО после установленной недоступности выплаты,
    а не по умолчанию.
    """
    c = _con()
    q = "SELECT provider,status,failure_reason,kyc_required,account_ready FROM payment_routes WHERE 1=1"
    args = []
    for col, val in (("currency", currency), ("network", network), ("provider", provider)):
        if val:
            q += f" AND {col}=?"
            args.append(val)
    rows = c.execute(q, args).fetchall()
    c.close()
    if not rows:
        return None, "такого маршрута в реестре нет — это незнание, а не отказ"
    ok = [r for r in rows if r[1] == "verified"]
    if ok:
        return True, f"маршрут проверен: {ok[0][0]}"
    blocked = [r for r in rows if r[1] == "blocked"]
    if blocked and len(blocked) == len(rows):
        return False, f"выплата недоступна: {blocked[0][2] or 'причина не записана'}"
    return None, "маршрут есть, но не проверен — проверить до того, как вкладывать труд"


def mark(provider, method=None, currency=None, network=None, **fields):
    """Записывает результат проверки маршрута. Без даты проверки не принимается.

    Маршрут, объявленный рабочим без отметки о том, КОГДА это установлено, —
    это обещание. Через месяц оно неотличимо от факта.
    """
    if "status" in fields and fields["status"] == "verified" and not fields.get("verified_at"):
        fields["verified_at"] = now()
    sets = ", ".join(f"{k}=?" for k in fields)
    args = list(fields.values())
    q = f"UPDATE payment_routes SET {sets} WHERE provider=?"
    args.append(provider)
    for col, val in (("method", method), ("currency", currency), ("network", network)):
        if val:
            q += f" AND {col}=?"
            args.append(val)
    c = _con()
    write(c, q, args)
    c.commit(); c.close()
    return True


# ═════════════════════════════════════════════ ЗАПРОС И ПОСТУПЛЕНИЕ
def request_payment(opportunity, amount, currency, route_id=None, instructions=None):
    """Создаёт запрос оплаты. Запрос — это НЕ платёж, и путать нельзя."""
    c = _con()
    cur = c.execute(
        "INSERT INTO payment_requests(route_id,opportunity,amount,currency,"
        "instructions,requested_at) VALUES (?,?,?,?,?,?)",
        (route_id, opportunity, amount, currency, instructions, now()))
    c.commit()
    rid = cur.lastrowid
    c.close()
    return rid


def record_receipt(proof, proof_kind, gross, currency, fees=0.0,
                   request_id=None, route_id=None, from_party=None):
    """Записывает ПОЛУЧЕННЫЕ деньги. Только с настоящим доказательством.

    Три правила, каждое из горького опыта:
      * вид доказательства обязан быть из списка — «подтверждено» словом не
        является доказательством;
      * доказательство уникально, повторная запись невозможна: так исключается
        двойной учёт одного и того же перевода;
      * платёж от самого владельца не засчитывается — он удовлетворил бы
        таблицу и сделал бы ложным всё заявление миссии.
    """
    if proof_kind not in PROOF_KINDS:
        raise ValueError(f"неизвестный вид доказательства: {proof_kind}. "
                         f"Настоящие: {', '.join(PROOF_KINDS)}")
    if not proof or len(str(proof).strip()) < 8:
        raise ValueError("доказательство слишком короткое, чтобы быть проверяемым")
    if from_party and from_party.lower() in {v.lower() for v in OWNER_DESTINATIONS.values()}:
        raise ValueError("платёж от самого владельца прибылью не является")

    net = round(float(gross) - float(fees or 0), 8)
    c = _con()
    try:
        c.execute(
            "INSERT INTO payment_receipts(request_id,route_id,gross,fees,net,currency,"
            "proof,proof_kind,from_party,received_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (request_id, route_id, gross, fees or 0, net, currency,
             str(proof), proof_kind, from_party, now()))
        c.commit()
    except Exception as e:
        c.close()
        if "UNIQUE" in str(e):
            return {"ok": False, "why": "это доказательство уже записано — двойной учёт"}
        raise
    c.close()
    return {"ok": True, "gross": gross, "fees": fees or 0, "net": net,
            "currency": currency}


def totals():
    """Сколько ДЕЙСТВИТЕЛЬНО получено, по валютам. Обещания сюда не входят."""
    c = _con()
    rows = c.execute(
        "SELECT currency, COUNT(*), SUM(gross), SUM(fees), SUM(net) "
        "FROM payment_receipts GROUP BY currency").fetchall()
    c.close()
    return [{"валюта": r[0], "платежей": r[1], "gross": r[2],
             "комиссии": r[3], "net": r[4]} for r in rows]


def state_of_money():
    """Шесть состояний денег, каждое своим числом. Смешивать их нельзя."""
    c = _con()

    def q(sql):
        try:
            return (c.execute(sql).fetchone() or [0])[0] or 0
        except Exception:
            return 0

    out = {
        "обещано": q("SELECT COUNT(*) FROM payment_requests "
                     "WHERE state='PAYMENT_REQUESTED'"),
        "начислено": q("SELECT COUNT(*) FROM platform_balances WHERE amount > 0"),
        "выводимо": q("SELECT COUNT(*) FROM platform_balances "
                      "WHERE withdrawable=1 AND amount > 0"),
        "получено": q("SELECT COUNT(*) FROM payment_receipts"),
        "выведено": q("SELECT COUNT(*) FROM withdrawal_status WHERE state='WITHDRAWN'"),
    }
    c.close()
    return out


def routes(status=None):
    c = _con()
    q = ("SELECT provider,method,currency,network,status,kyc_required,"
         "withdrawal_available,failure_reason,verified_at FROM payment_routes")
    args = ()
    if status:
        q += " WHERE status=?"
        args = (status,)
    rows = c.execute(q + " ORDER BY provider", args).fetchall()
    c.close()
    keys = ("provider", "method", "currency", "network", "status",
            "kyc", "withdrawal", "failure", "verified_at")
    return [dict(zip(keys, r)) for r in rows]


if __name__ == "__main__":
    n = seed()
    print("=" * 74)
    print(f"МАРШРУТЫ ПОЛУЧЕНИЯ ОПЛАТЫ — заведено новых: {n}")
    print("=" * 74)
    for r in routes():
        print(f"  [{r['status']:<10}] {r['provider']:<20} {r['method']:<16} "
              f"{r['currency'] or '-':<6} {r['network'] or '-'}")
    print()
    print("СОСТОЯНИЯ ДЕНЕГ:", json.dumps(state_of_money(), ensure_ascii=False))
    print("ПОЛУЧЕНО:", json.dumps(totals(), ensure_ascii=False) or "ничего")
