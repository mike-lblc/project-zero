"""ПРОДАВЕЦ — находит у конкретного лида конкретную проблему и готовит предложение.

Почему это работает там, где не работал продукт: у нас есть ДАННЫЕ ЭТИХ ЖЕ КОМПАНИЙ.
Мы не пишем «здравствуйте, предлагаем услуги» — мы показываем человеку то, чего он
про себя не знает, с цифрами из публичного индекса.

И почему именно они: операторы x402 по определению имеют кошелёк и платят криптой
нативно. Проблема расчётов с РФ, которая закрывала обычные биржи фриланса, здесь
не возникает вовсе.

Обнаруживаемые проблемы (всё считается по фактам, ничего не выдумывается):
  MISSING_MCP     — сервисы не в реестре MCP: целый канал обнаружения не используется
  DEAD_WEIGHT     — эндпоинты с вызовами, но без единого плательщика: не монетизированы
  GOING_QUIET     — были плательщики, вызовов почти нет: сервис умирает
  MISPRICED       — цена сильно ниже медианы своей категории: недобирают деньги
  NO_DESCRIPTION  — пустое описание: агент не поймёт, что покупает

Границы: работаем только с публичными деловыми данными. Письмо отправляет ВЛАДЕЛЕЦ
от своего имени — рассылка ботом это спам и потеря репутации домена.
"""
import sys, json, urllib.request, statistics
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.db import connect
from core import guard, bus

ROOT = Path(__file__).resolve().parent.parent
IDX = ROOT / "data" / "bazaar_index.json"

SCHEMA = """
CREATE TABLE IF NOT EXISTS lead_problems (
  id INTEGER PRIMARY KEY,
  domain TEXT NOT NULL,
  problem TEXT NOT NULL,
  evidence TEXT NOT NULL,
  severity INTEGER NOT NULL,
  service_offer TEXT NOT NULL,
  price_usd REAL,
  found_at TEXT NOT NULL,
  UNIQUE(domain, problem)
);
"""

# Что мы умеем и по какой цене. Цены — за работу, а не за вызов API:
# здесь чек в десятки долларов, а не в центы.
OFFERS = {
    "MISSING_MCP":    ("Публикация ваших эндпоинтов как MCP-сервера в официальном реестре — "
                       "отдельный канал, где агенты ищут инструменты сами", 120.0),
    "DEAD_WEIGHT":    ("Аудит немонетизированных эндпоинтов: почему их вызывают, но не платят", 80.0),
    "GOING_QUIET":    ("Разбор падения спроса по вашей категории с данными конкурентов", 60.0),
    "MISPRICED":      ("Пересмотр цен по фактическим ценам вашей категории", 60.0),
    "NO_DESCRIPTION": ("Переписывание описаний под машинный поиск: агент выбирает по метаданным, "
                       "а не по красоте текста", 40.0),
}


def now():
    return datetime.now(timezone.utc).isoformat()


def _con():
    c = connect()
    c.executescript(SCHEMA)
    return c


def _index():
    try:
        return json.loads(IDX.read_text(encoding="utf-8"))
    except Exception:
        return []


def in_mcp_registry(domain):
    """Есть ли компания в реестре MCP. ПОИСКОМ по имени, а не сканом первых 100 записей.

    Прошлая версия тянула ?limit=100 и искала домен в адресе remotes. Она врала:
    реестр гораздо больше сотни, а записи именуются io.github.<кто>/<что>, поэтому
    домен там может не встречаться вовсе. Из-за этого диагноз «вас нет в реестре»
    оказался ЛОЖНЫМ для 4 компаний из 5 — включая ту, которой готовилось письмо.
    Ошибочное утверждение в первой строке письма убивает доверие мгновенно.

    Возвращает True / False / None. None = реестр не ответил, и тогда мы
    НЕ УТВЕРЖДАЕМ ничего.
    """
    import time
    base = domain.split(".")[-2] if domain.count(".") >= 2 else domain.split(".")[0]
    for q in {base, domain.split(".")[0], domain.replace(".", "-")}:
        for attempt in range(2):
            try:
                r = urllib.request.urlopen(urllib.request.Request(
                    "https://registry.modelcontextprotocol.io/v0/servers?search=" + q,
                    headers={"User-Agent": "P0-salesman/0.1", "Accept": "application/json"}),
                    timeout=25)
                if json.loads(r.read().decode()).get("servers"):
                    return True
                break
            except Exception:
                time.sleep(1.5)
        else:
            return None
    return False


def diagnose(limit_domains=12):
    """Ставит диагноз каждому лиду по его собственным данным."""
    guard.check_action("research", "GREEN")
    from agents.leads import operators, _domain
    items = _index()
    ops = operators()
    if not ops:
        return "нет данных"

    # медианная цена по категориям — эталон для проверки на недооценку
    cat_prices = {}
    for it in items:
        a = (it.get("accepts") or [{}])[0]
        try:
            p = int(a.get("maxAmountRequired") or a.get("amount")) / 1e6
        except Exception:
            continue
        for t in (it.get("tags") or []):
            cat_prices.setdefault(t, []).append(p)
    cat_med = {t: statistics.median(v) for t, v in cat_prices.items() if len(v) >= 5}

    # проверяется по каждому домену отдельно, ниже
    by_domain = {}
    for it in items:
        d = _domain(it.get("resource"))
        if d:
            by_domain.setdefault(d, []).append(it)

    top = sorted(ops.items(), key=lambda kv: -(kv[1]["payers"]))[:limit_domains]
    c = _con()
    found = 0
    for domain, o in top:
        svc = by_domain.get(domain, [])
        problems = []

        reg = in_mcp_registry(domain)
        if reg is False:
            problems.append(("MISSING_MCP",
                             f"{len(svc)} эндпоинтов, в реестре MCP не найдены — "
                             f"канал обнаружения не используется", 4))
        # reg is True  -> они там есть, беспокоить нечем
        # reg is None  -> реестр не ответил, НЕ УТВЕРЖДАЕМ

        dead = [s for s in svc if (s.get("quality") or {}).get("l30DaysTotalCalls", 0) > 20
                and not (s.get("quality") or {}).get("l30DaysUniquePayers")]
        if dead:
            problems.append(("DEAD_WEIGHT",
                             f"{len(dead)} эндпоинтов вызываются, но не имеют ни одного "
                             f"плательщика за 30 дней", 5))

        quiet = [s for s in svc if (s.get("quality") or {}).get("l30DaysUniquePayers", 0) > 3
                 and (s.get("quality") or {}).get("l30DaysTotalCalls", 0) < 10]
        if quiet:
            problems.append(("GOING_QUIET",
                             f"{len(quiet)} эндпоинтов с плательщиками, но почти без вызовов", 3))

        under = []
        for s in svc:
            a = (s.get("accepts") or [{}])[0]
            try:
                p = int(a.get("maxAmountRequired") or a.get("amount")) / 1e6
            except Exception:
                continue
            meds = [cat_med[t] for t in (s.get("tags") or []) if t in cat_med]
            if meds and p > 0 and p < statistics.median(meds) / 3:
                under.append(s)
        if len(under) >= 3:
            problems.append(("MISPRICED",
                             f"{len(under)} эндпоинтов дешевле трети медианы своей категории", 3))

        nodesc = [s for s in svc if not (s.get("description") or "").strip()]
        if len(nodesc) >= 5:
            problems.append(("NO_DESCRIPTION",
                             f"{len(nodesc)} эндпоинтов без описания — агент не поймёт, "
                             f"что покупает", 2))

        for code, evidence, sev in problems:
            offer, price = OFFERS[code]
            c.execute("""INSERT INTO lead_problems(domain,problem,evidence,severity,
                         service_offer,price_usd,found_at) VALUES (?,?,?,?,?,?,?)
                         ON CONFLICT(domain,problem) DO UPDATE SET evidence=?, severity=?""",
                      (domain, code, evidence, sev, offer, price, now(), evidence, sev))
            found += 1
    c.commit()
    total = c.execute("SELECT COUNT(*) FROM lead_problems").fetchone()[0]
    value = c.execute("SELECT SUM(price_usd) FROM lead_problems").fetchone()[0] or 0
    c.close()
    bus.broadcast("salesman", f"Поставил диагноз {len(top)} компаниям: найдено {found} доказуемых "
                              f"проблем. Суммарная стоимость работ по ним ${value:.0f}. "
                              f"Это чеки в десятки долларов, а не центы за вызов.")
    return f"проблем найдено: {total}, потенциал ${value:.0f}"


def pitch_for(domain):
    """Готовит письмо под конкретную компанию. Только факты из их же данных."""
    c = _con()
    rows = c.execute("SELECT problem,evidence,severity,service_offer,price_usd FROM lead_problems "
                     "WHERE domain=? ORDER BY severity DESC", (domain,)).fetchall()
    c.close()
    if not rows:
        return None
    top = rows[0]
    lines = [f"Тема: {domain} — {top[1]}", "",
             "Здравствуйте.", "",
             f"Я собираю данные по рынку x402 и обратил внимание на {domain}.",
             "Несколько наблюдений по вашим эндпоинтам, всё из публичного индекса:", ""]
    for prob, ev, sev, offer, price in rows[:3]:
        lines.append(f"  • {ev}")
    lines += ["",
              f"Первое можно закрыть быстро: {top[3]}. Цена — ${top[4]:.0f}.",
              "",
              "Если интересно, покажу подробности по вашим конкретным эндпоинтам.",
              "Мы сами прошли этот путь: наш сервис опубликован в официальном реестре MCP,",
              "могу показать, как это делается.", ""]
    txt = "\n".join(lines)
    out = ROOT / "ops" / "pitches"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{domain.replace('.', '_')}.txt").write_text(txt, encoding="utf-8")
    return txt


def pipeline():
    """Весь список готовых обращений."""
    c = _con()
    doms = [r[0] for r in c.execute(
        "SELECT domain, SUM(price_usd) v FROM lead_problems GROUP BY domain ORDER BY v DESC")]
    c.close()
    made = [d for d in doms if pitch_for(d)]
    if made:
        bus.broadcast("salesman", f"Готово {len(made)} персональных обращений в ops/pitches/. "
                                  f"Отправляет владелец от своего имени — рассылка ботом это спам "
                                  f"и мгновенная потеря репутации домена.")
    return made


CYCLE = [("diagnose_leads", lambda: diagnose(8))]


if __name__ == "__main__":
    print(diagnose())
    print()
    c = _con()
    print("═══ ДИАГНОЗЫ ПО КОМПАНИЯМ ═══")
    for r in c.execute("""SELECT domain, COUNT(*) n, SUM(price_usd) v
                          FROM lead_problems GROUP BY domain ORDER BY v DESC LIMIT 12"""):
        print(f"  ${r[2]:>6.0f}  {r[0][:40]:40} проблем: {r[1]}")
    print()
    print("═══ ЧТО ИМЕННО НАЙДЕНО ═══")
    for r in c.execute("""SELECT domain, problem, evidence FROM lead_problems
                          ORDER BY severity DESC LIMIT 8"""):
        print(f"  {r[0][:28]:28} {r[1]:16} {r[2][:56]}")
    c.close()
    print()
    made = pipeline()
    print(f"═══ ГОТОВО ОБРАЩЕНИЙ: {len(made)} (в ops/pitches/) ═══")
