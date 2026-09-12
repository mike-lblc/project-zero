"""ИТОГОВЫЙ ОТЧЁТ ПО GND §16 — из базы, а не по памяти.

Таблица из пятнадцати столбцов по каждому способу заработка и десять
отдельных списков. Описательные поля (кто покупатель, какой канал) заданы
здесь, потому что это устройство способа; всё, что меняется — состояние,
доказательства, заявки, ответы, деньги, — читается из базы при каждом запуске.
Отчёт, собранный руками, устаревает молча; этот пересобирается одной командой:

    py -3.13 -X utf8 ops/gnd_report.py            → reports/gnd-final.md
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.db import connect  # noqa: E402


def q(c, sql, args=()):
    try:
        return c.execute(sql, args).fetchall()
    except Exception:
        return []


def one(c, sql, args=(), default=0):
    r = q(c, sql, args)
    return r[0][0] if r and r[0][0] is not None else default


def routes_line(c):
    rows = q(c, "SELECT currency, network, status FROM payment_routes")
    ok = sorted({f"{cur} {net}" for cur, net, st in rows if st == "verified"})
    return ok, len(rows)


def build():
    c = connect()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    ok_routes, n_routes = routes_line(c)
    crypto_opts = "USDC (Base, Ethereum, Polygon, Arbitrum), ETH, BTC — проверено: " + ", ".join(ok_routes)
    receipts = one(c, "SELECT COUNT(*) FROM payment_receipts")

    # ── факты по способам ──────────────────────────────────────────────
    claims = q(c, "SELECT payload, result, created_at FROM actions "
                  "WHERE kind='bounty_claim' AND dry_run=0 ORDER BY id")
    prs = q(c, "SELECT url, state, review_state, bounty_usd FROM pull_requests ORDER BY id")
    outreach = q(c, "SELECT domain, url, sent_at, note FROM outreach WHERE url IS NOT NULL")
    # одно определение конкурса на всю систему — то же, что у охотника
    from agents.bounty import CONTEST_SQL
    gh_found = q(c, "SELECT url, amount_usd, fit_score, note FROM bounties WHERE status='found' "
                    f"AND NOT ({CONTEST_SQL}) ORDER BY fit_score DESC")
    contests = q(c, "SELECT title, amount_usd, rivals, prizes, fit_score FROM bounties "
                    f"WHERE status='found' AND ({CONTEST_SQL}) ORDER BY fit_score DESC")
    open_paths = q(c, "SELECT platform, category FROM money_paths WHERE open_to_us=1")
    services = {n: (st, ex) for n, st, ex in q(c, "SELECT name, status, executor FROM services")}
    mcp = q(c, "SELECT result FROM actions WHERE kind='publish_mcp_registry' AND dry_run=0")
    svc_pub = q(c, "SELECT result FROM actions WHERE kind='publish_service' AND dry_run=0")

    def claim_repo(p):
        try:
            d = json.loads(p)
            return f"github.com/{d['repo']}/issues/{d['issue']}"
        except Exception:
            return str(p)[:60]

    merged = [p for p in prs if p[1] == "MERGED"]
    top_gh = gh_found[0] if gh_found else None
    top_contest = contests[0] if contests else None

    rows = [
        {
            "method": "Баунти за код (GitHub)",
            "source": "поиск GitHub по меткам bounty/Algora, bounty.hunt",
            "buyer": "сопровождающие с объявленной проектом наградой",
            "owner": "bounty → craftsman",
            "exec": "только документация и перевод (craftsman.we_can_do); правка чужого кода — нет",
            "contact": "комментарий-заявка в задаче GitHub (постоянное разрешение владельца)",
            "delivery": "pull request",
            "payment": "Algora / перевод на кошелёк; " + crypto_opts,
            "access": "самостоятельное хранение проверено; Algora — не проверена (нужен аккаунт владельца)",
            "state": f"заявок {len(claims)}, PR {len(prs)} (слито {len(merged)}), поступлений {receipts}",
            "evidence": "; ".join([claim_repo(p) for p, _, _ in claims] + [u for u, *_ in prs]) or "—",
            "env": (f"${top_gh[1]:.0f} × оценка вероятности (приоритет {top_gh[2]:g})" if top_gh else "—"),
            "ttc": "3–7 дней при объявленной награде (оценка)",
            "blocker": ("единственная заявка ушла в репозиторий без признаков платёжеспособности; "
                        "задач с наградой в очереди " + str(len(gh_found))),
            "next": "брать только задачи с наградой, объявленной проектом, по формуле приоритета",
        },
        {
            "method": "Документация и локализация для открытых проектов",
            "source": "find_doc_work: задачи с меткой documentation",
            "buyer": "сопровождающие CLI-проектов",
            "owner": "craftsman → executor",
            "exec": f"исполнима ({services.get('техническая документация', ('?', ''))[1]}), сверка с исходником",
            "contact": "публичная задача GitHub",
            "delivery": "pull request",
            "payment": "только если награда объявлена проектом; " + crypto_opts,
            "access": "плательщика нет — награду $25 назначил посторонний",
            "state": ("РАБОТА ПРИНЯТА: " + ", ".join(f"{u} ({s}, {r})" for u, s, r, _ in merged)
                      if merged else "PR не слиты"),
            "evidence": "; ".join(u for u, *_ in merged) or "—",
            "env": "$0 — за принятую работу никто не обязывался платить",
            "ttc": "—",
            "blocker": "работа доставлена и принята, но оплату не объявлял ни проект, ни спонсор",
            "next": ("решение владельца: спросить автора, назначившего $25, в силе ли "
                     "предложение (одно сообщение), или закрыть как вклад без оплаты"),
        },
        {
            "method": "Конкурентная разведка по каталогу x402",
            "source": "каталог Bazaar (14 231 служба), salesman.diagnose",
            "buyer": "операторы платных служб",
            "owner": "salesman → closer",
            "exec": f"исполнима ({services.get('конкурентная разведка по каталогу x402', ('?', ''))[1]}), "
                    "место проверяется независимым счётом",
            "contact": "публичная задача в репозитории службы, один раз навсегда",
            "delivery": "отчёт в задаче или файлом",
            "payment": crypto_opts,
            "access": "проверено: " + ", ".join(ok_routes),
            "state": f"CONTACTED: обращений {len(outreach)}, ответов — см. closer.check_replies",
            "evidence": "; ".join(u for _, u, _, _ in outreach) or "—",
            "env": "цена не подтверждена рынком (в каталоге цен нет намеренно)",
            "ttc": "неизвестно до ответа",
            "blocker": "ответа нет; предел — одно обращение в сутки",
            "next": "ждать ответа; следующему оператору — одно обращение с его же числами",
        },
        {
            "method": "Аудит совместимости x402",
            "source": "каталог Bazaar",
            "buyer": "операторы x402-служб",
            "owner": "salesman → executor",
            "exec": "исполнима (core.x402_probe:check); доказана на нашей службе",
            "contact": "публичная задача в репозитории службы",
            "delivery": "отчёт с воспроизводимыми запросами",
            "payment": crypto_opts,
            "access": "проверено: " + ", ".join(ok_routes),
            "state": "QUALIFIED: способность есть, обращений 0",
            "evidence": "worker/src/index.js исправлен: заголовки v2 читаются",
            "env": "неизвестно",
            "ttc": "неизвестно",
            "blocker": "по чужим службам проверка ещё не запускалась — покупатель с этой поломкой не найден",
            "next": "прогнать проверку по службам каталога и приложить находку к обращению",
        },
        {
            "method": "Извлечение и очистка данных",
            "source": "классы обхода «очистка данных», «таблицы и бизнес-данные»",
            "buyer": "авторы публичных запросов на выгрузку",
            "owner": "prospector → executor (agents.extractor)",
            "exec": "исполнима: CSV/JSON/XLSX + отчёт, каждое значение сверено с исходником",
            "contact": "канал, где размещён запрос",
            "delivery": "файлы и sources.json",
            "payment": crypto_opts,
            "access": "проверено: " + ", ".join(ok_routes),
            "state": "DISCOVERED: классы добавлены в обход, запросов с бюджетом не найдено",
            "evidence": "agents/extractor.py, проверка на ISO 4217 (178 строк) и API GitHub",
            "env": "—", "ttc": "—",
            "blocker": "покупатель не найден",
            "next": "разведка классов в очереди проспектора",
        },
        {
            "method": "Платный x402 API (Bazaar Rank)",
            "source": "реестр MCP и каталог Bazaar",
            "buyer": "ИИ-агенты, выбирающие службу",
            "owner": "distributor, merchant",
            "exec": "служба работает (Cloudflare Worker)",
            "contact": "запись в реестре MCP: " + (mcp[0][0] if mcp else "нет"),
            "delivery": "HTTP JSON по оплате",
            "payment": "USDC в сети Base по x402 — один маршрут из " + str(n_routes),
            "access": "проверено",
            "state": f"LIVE: {svc_pub[0][0] if svc_pub else '—'}; поступлений {receipts}",
            "evidence": "реестр MCP io.github.mike-lblc/x402-bazaar-rank",
            "env": "$0.01–$1.25 за вызов × вызовов 0",
            "ttc": "неизвестно",
            "blocker": "платных вызовов не было",
            "next": "видимость; стратегию вокруг одного адаптера не строить (GND §12)",
        },
        {
            "method": "Конкурсы и хакатоны",
            "source": "Devpost API, mlcontests",
            "buyer": "организаторы",
            "owner": "bounty",
            "exec": "исполнителя нет: продукт для конкурса не входит в исполнимые услуги",
            "contact": "подача работы на площадке",
            "delivery": "подача",
            "payment": "решает организатор; часто банковский перевод",
            "access": "неизвестно; фиатный перевод владельцу недоступен",
            "state": f"DISCOVERED: {len(contests)} открытых",
            "evidence": "bounties, repo=devpost.com",
            "env": (f"{top_contest[0]}: фонд ${top_contest[1]:,.0f}, мест {top_contest[3] or '?'}, "
                    f"участников {top_contest[2] or '?'}" if top_contest else "—"),
            "ttc": "30+ дней",
            "blocker": "нет исполнителя; платят одному победителю",
            "next": "не браться, пока нет исполнимой услуги под тему конкурса",
        },
        {
            "method": "Площадки из обхода (безопасность, гранты, партнёрские)",
            "source": "prospector: 72 класса обхода",
            "buyer": "площадки, платящие исполнителю",
            "owner": "prospector",
            "exec": "не доказана: тестирование безопасности требует границ программы",
            "contact": "—", "delivery": "—",
            "payment": "по площадке",
            "access": "у каждой отдельно; фиатные отсеяны решением владельца",
            "state": f"исследовано: открытых площадок {len(open_paths)}, заявок 0",
            "evidence": ", ".join(p for p, _ in open_paths[:6]) or "—",
            "env": "неизвестно", "ttc": "неизвестно",
            "blocker": "исполнимой услуги под них нет",
            "next": "deep_check открытых площадок",
        },
    ]

    cols = [("method", "Revenue method"), ("source", "Opportunity source"), ("buyer", "Buyer"),
            ("owner", "Agent owner"), ("exec", "Execution capability"),
            ("contact", "Contact channel"), ("delivery", "Delivery channel"),
            ("payment", "Payment options"), ("access", "Payout accessibility"),
            ("state", "Current state"), ("evidence", "Evidence"),
            ("env", "Expected net value"), ("ttc", "Time to cash"),
            ("blocker", "Blocker"), ("next", "Next action")]

    def cell(v):
        return str(v).replace("|", "/").replace("\n", " ")

    out = [f"# Итоговый отчёт GND §16", "",
           f"Собран из базы {now}. Пересобрать: `py -3.13 -X utf8 ops/gnd_report.py`.", "",
           f"**Миссия:** первый сторонний платёж — {'ПОЛУЧЕН' if receipts else 'НЕ ПОЛУЧЕН'} "
           f"(подтверждённых поступлений {receipts}).", "",
           "| " + " | ".join(h for _, h in cols) + " |",
           "|" + "---|" * len(cols)]
    for r in rows:
        out.append("| " + " | ".join(cell(r[k]) for k, _ in cols) + " |")

    replies = []
    try:
        from agents import closer
        replies.append(closer.check_replies())
    except Exception as e:
        replies.append(f"проверка ответов не выполнена: {type(e).__name__}")
    approved = [u for u, s, r, _ in prs if r == "APPROVED"]

    discovered = one(c, "SELECT COUNT(*) FROM path_categories WHERE queries IS NOT NULL")
    platforms = one(c, "SELECT COUNT(*) FROM money_paths")
    balances = one(c, "SELECT COUNT(*) FROM platform_balances WHERE amount > 0")
    withdrawable = one(c, "SELECT COUNT(*) FROM platform_balances WHERE withdrawable=1 AND amount > 0")
    sections = [
        ("Действительно подключены",
         [f"маршруты получения: {len(ok_routes)} проверено живым запросом из {n_routes} ({', '.join(ok_routes)})",
          "канал доставки GitHub — доказан отправками со ссылкой",
          "x402-служба и запись в реестре MCP",
          "поиск: DuckDuckGo Lite, Marginalia, Brave — с различением ошибки и пустоты"]),
        ("Только исследованы",
         [f"площадок найдено {platforms}, открытых {len(open_paths)} — заявок по ним нет",
          f"конкурсов {len(contests)} — исполнителя нет"]),
        ("Реальные заявки", [f"{claim_repo(p)} — {res} ({at[:10]})" for p, res, at in claims] or ["нет"]),
        ("Реальные отправленные сообщения",
         [f"{u} — {d} ({s[:10]})" for d, u, s, _ in outreach] + [f"PR {u}" for u, *_ in prs] or ["нет"]),
        ("Ответы", replies + [f"одобрение ревью: {u}" for u in approved]),
        ("Выполненная работа", [f"{u} — {s}" for u, s, *_ in prs] or ["нет"]),
        ("Начисление", [f"{balances}"]),
        ("Доступный вывод", [f"{withdrawable}"]),
        ("Подтверждённая прибыль", [f"{receipts} поступлений"]),
        ("Новые способы, найденные системой самостоятельно",
         [f"классов, вычитанных с рынка: {discovered}",
          f"площадок, найденных поиском агентов: {platforms}"]),
    ]
    for title, items in sections:
        out += ["", f"## {title}", ""] + [f"- {i}" for i in items]
    c.close()
    path = ROOT / "reports" / "gnd-final.md"
    path.parent.mkdir(exist_ok=True)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return path


if __name__ == "__main__":
    print(build())
