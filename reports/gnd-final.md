# Итоговый отчёт GND §16

Собран из базы 2026-09-13 12:59 UTC. Пересобрать: `py -3.13 -X utf8 ops/gnd_report.py`.

**Миссия:** первый сторонний платёж — НЕ ПОЛУЧЕН (подтверждённых поступлений 0).

**Осознанное отступление от GND §3:** фиатные выплаты (PayPal, Stripe, банковский перевод) отклоняются до оценки по решению владельца — он резидент РФ, и эти способы ему недоступны фактически. Отступление записано в agents/bounty.py.

| Revenue method | Opportunity source | Buyer | Agent owner | Execution capability | Contact channel | Delivery channel | Payment options | Payout accessibility | Current state | Evidence | Expected net value | Time to cash | Blocker | Next action |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Баунти за код (GitHub) | поиск GitHub по меткам bounty/Algora, bounty.hunt | сопровождающие с объявленной проектом наградой | bounty → craftsman | только документация и перевод (craftsman.we_can_do); правка чужого кода — нет | комментарий-заявка в задаче GitHub (постоянное разрешение владельца) | pull request | Algora / перевод на кошелёк; USDC (Base, Ethereum, Polygon, Arbitrum), ETH, BTC — проверено: BTC bitcoin, ETH arbitrum, ETH base, ETH ethereum, POL polygon, USDC arbitrum, USDC base, USDC ethereum, USDC polygon, USDC.e arbitrum, USDC.e polygon | самостоятельное хранение проверено; Algora — не проверена (нужен аккаунт владельца) | заявок 1, PR 1 (слито 1), поступлений 0; сделки — баунти за код: CONTACTED 1 | github.com/cuentaprueba244w-dotcom/zeroeye/issues/3; https://github.com/BasedHardware/omi/pull/13455 | — | 3–7 дней при объявленной награде (оценка) | единственная заявка ушла в репозиторий без признаков платёжеспособности; задач с наградой в очереди 0 | брать только задачи с наградой, объявленной проектом, по формуле приоритета |
| Документация и локализация для открытых проектов | find_doc_work: задачи с меткой documentation | сопровождающие CLI-проектов | craftsman → executor | исполнима (agents.executor:produce), сверка с исходником | публичная задача GitHub | pull request | только если награда объявлена проектом; USDC (Base, Ethereum, Polygon, Arbitrum), ETH, BTC — проверено: BTC bitcoin, ETH arbitrum, ETH base, ETH ethereum, POL polygon, USDC arbitrum, USDC base, USDC ethereum, USDC polygon, USDC.e arbitrum, USDC.e polygon | плательщика нет — награду $25 назначил посторонний | РАБОТА ПРИНЯТА: https://github.com/BasedHardware/omi/pull/13455 (MERGED, APPROVED); сделки — локализация документации: REPLIED 1 | https://github.com/BasedHardware/omi/pull/13455 | $0 — за принятую работу никто не обязывался платить | — | работа принята, но оплату не объявлял ни проект, ни спонсор; правила omi: награда по усмотрению, выплата только PayPal | вопрос об оплате отправлен сопровождающему с согласия владельца: https://github.com/BasedHardware/omi/pull/13455#issuecomment-5651798730 — ждём ответа, повторно не пишем |
| Конкурентная разведка по каталогу x402 | каталог Bazaar (14 231 служба), salesman.diagnose | операторы платных служб | salesman → closer | исполнима (agents.outreach:rank_of), место проверяется независимым счётом | публичная задача в репозитории службы, один раз навсегда | отчёт в задаче или файлом | USDC (Base, Ethereum, Polygon, Arbitrum), ETH, BTC — проверено: BTC bitcoin, ETH arbitrum, ETH base, ETH ethereum, POL polygon, USDC arbitrum, USDC base, USDC ethereum, USDC polygon, USDC.e arbitrum, USDC.e polygon | проверено: BTC bitcoin, ETH arbitrum, ETH base, ETH ethereum, POL polygon, USDC arbitrum, USDC base, USDC ethereum, USDC polygon, USDC.e arbitrum, USDC.e polygon | сделки — конкурентная разведка по каталогу x402: CONTACTED 1 | https://github.com/tempoxyz/mpp-go/issues/157; https://github.com/BasedHardware/omi/pull/13455#issuecomment-5651798730 | цена не подтверждена рынком (в каталоге цен нет намеренно) | неизвестно до ответа | ответа нет; предел — одно обращение в сутки | ждать ответа; следующему оператору — одно обращение с его же числами |
| Аудит совместимости x402 | каталог Bazaar | операторы x402-служб | salesman → executor | исполнима (core.x402_probe:check); доказана на нашей службе | публичная задача в репозитории службы | отчёт с воспроизводимыми запросами | USDC (Base, Ethereum, Polygon, Arbitrum), ETH, BTC — проверено: BTC bitcoin, ETH arbitrum, ETH base, ETH ethereum, POL polygon, USDC arbitrum, USDC base, USDC ethereum, USDC polygon, USDC.e arbitrum, USDC.e polygon | проверено: BTC bitcoin, ETH arbitrum, ETH base, ETH ethereum, POL polygon, USDC arbitrum, USDC base, USDC ethereum, USDC polygon, USDC.e arbitrum, USDC.e polygon | QUALIFIED: способность есть, обращений 0 | worker/src/index.js исправлен: заголовки v2 читаются | неизвестно | неизвестно | по чужим службам проверка ещё не запускалась — покупатель с этой поломкой не найден | прогнать проверку по службам каталога и приложить находку к обращению |
| Извлечение и очистка данных | классы обхода «очистка данных», «таблицы и бизнес-данные» | авторы публичных запросов на выгрузку | prospector → executor (agents.extractor) | исполнима: CSV/JSON/XLSX + отчёт, каждое значение сверено с исходником | канал, где размещён запрос | файлы и sources.json | USDC (Base, Ethereum, Polygon, Arbitrum), ETH, BTC — проверено: BTC bitcoin, ETH arbitrum, ETH base, ETH ethereum, POL polygon, USDC arbitrum, USDC base, USDC ethereum, USDC polygon, USDC.e arbitrum, USDC.e polygon | проверено: BTC bitcoin, ETH arbitrum, ETH base, ETH ethereum, POL polygon, USDC arbitrum, USDC base, USDC ethereum, USDC polygon, USDC.e arbitrum, USDC.e polygon | DISCOVERED: классы добавлены в обход, запросов с бюджетом не найдено | agents/extractor.py, проверка на ISO 4217 (178 строк) и API GitHub | — | — | покупатель не найден | разведка классов в очереди проспектора |
| Платный x402 API (Bazaar Rank) | реестр MCP и каталог Bazaar | ИИ-агенты, выбирающие службу | distributor, merchant | служба работает (Cloudflare Worker) | запись в реестре MCP: опубликовано, статус active | HTTP JSON по оплате | USDC в сети Base по x402 — один маршрут из 22 | проверено | LIVE: LIVE, 12/12 402 correct; поступлений 0 | реестр MCP io.github.mike-lblc/x402-bazaar-rank | $0.01–$1.25 за вызов × вызовов 0 | неизвестно | платных вызовов не было | видимость; стратегию вокруг одного адаптера не строить (GND §12) |
| Конкурсы и хакатоны | Devpost API, mlcontests | организаторы | bounty | исполнителя нет: продукт для конкурса не входит в исполнимые услуги | подача работы на площадке | подача | решает организатор; часто банковский перевод | неизвестно; фиатный перевод владельцу недоступен | DISCOVERED: 15 открытых | bounties, repo=devpost.com | Frontier Cascadia: фонд $66,000, мест 9, участников 77 | 30+ дней | нет исполнителя; платят одному победителю | не браться, пока нет исполнимой услуги под тему конкурса |
| Площадки из обхода (безопасность, гранты, партнёрские) | prospector: 72 класса обхода | площадки, платящие исполнителю | prospector | не доказана: тестирование безопасности требует границ программы | — | — | по площадке | у каждой отдельно; фиатные отсеяны решением владельца | исследовано: открытых площадок 11, заявок 0 | cointracker.io, immunefi.com, coingabbar.com, solanacompass.com, nevermined.ai, aurpay.net | неизвестно | неизвестно | исполнимой услуги под них нет | deep_check открытых площадок |

## Действительно подключены

- маршруты получения: 11 проверено живым запросом из 22 (BTC bitcoin, ETH arbitrum, ETH base, ETH ethereum, POL polygon, USDC arbitrum, USDC base, USDC ethereum, USDC polygon, USDC.e arbitrum, USDC.e polygon)
- канал доставки GitHub — доказан отправками со ссылкой
- x402-служба и запись в реестре MCP
- поиск: DuckDuckGo Lite, Marginalia, Brave — с различением ошибки и пустоты

## Только исследованы

- площадок найдено 542, открытых 11 — заявок по ним нет
- конкурсов 15 — исполнителя нет

## Реальные заявки

- github.com/cuentaprueba244w-dotcom/zeroeye/issues/3 — отправлено (2026-09-12)

## Реальные отправленные сообщения

- https://github.com/tempoxyz/mpp-go/issues/157 — api.onesource.io (2026-09-12)
- https://github.com/BasedHardware/omi/pull/13455#issuecomment-5651798730 — BasedHardware/omi (2026-09-13)
- PR https://github.com/BasedHardware/omi/pull/13455

## Ответы

- проверено обращений: 2; ответов пока нет
- Сделка: BasedHardware/omi#13455 — docs(cli): add Russian quickstart for omi-cli: PR слит и одобрен сопровождающим kodjima33 12.09.2026: https://github.com/BasedHardware/omi/pull/13455

## Выполненная работа

- https://github.com/BasedHardware/omi/pull/13455 — MERGED

## Начисление

- 0

## Доступный вывод

- 0

## Подтверждённая прибыль

- 0 поступлений

## Новые способы, найденные системой самостоятельно

- классов, вычитанных с рынка: 0
- площадок, найденных поиском агентов: 542
