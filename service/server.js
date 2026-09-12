import express from 'express';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createPaymentMiddleware } from '@openfacilitator/sdk';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.join(__dirname, '..');

// ---- config from .env (gitignored) ----
const env = Object.fromEntries(
  fs.readFileSync(path.join(ROOT, '.env'), 'utf8')
    .split('\n').filter(l => l.includes('=') && !l.trim().startsWith('#'))
    .map(l => [l.slice(0, l.indexOf('=')).trim(), l.slice(l.indexOf('=') + 1).trim()])
);
const PAY_TO = env.WALLET_ETH;
if (!/^0x[0-9a-fA-F]{40}$/.test(PAY_TO || '')) throw new Error('WALLET_ETH missing/invalid in .env');

const USDC_BASE = '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913';
const PRICE = '10000';          // $0.01 — медиана рынка (мы стояли в 10x ниже)

// ---- the asset: crawled Bazaar index with real usage metrics ----
// Каталог рынка весит 49 МБ и в репозиторий не кладётся: он пересобирается
// обходом. Значит, в облаке его нет — и раньше служба ИЗ-ЗА ЭТОГО НЕ
// ПОДНИМАЛАСЬ ВООБЩЕ, унося с собой и выдачу состояния, к рынку никак не
// относящуюся. Отсутствие товара на витрине — не повод закрывать магазин:
// без каталога поиск отвечает пустотой и честно говорит почему, а всё
// остальное работает.
const INDEX_FILE = path.join(ROOT, 'data', 'bazaar_index.json');
let raw = [];
let catalogNote = null;
try {
  raw = JSON.parse(fs.readFileSync(INDEX_FILE, 'utf8'));
} catch (e) {
  catalogNote = 'каталог рынка не загружен: ' + (e.code === 'ENOENT'
    ? 'файла нет — он пересобирается обходом и в репозиторий не кладётся'
    : e.message);
  console.warn(catalogNote);
}
const CATALOG = raw.map(it => {
  const a = (it.accepts || [])[0] || {};
  const q = it.quality || {};
  return {
    resource: it.resource || '',
    name: it.serviceName || null,
    description: (it.description || a.description || '').slice(0, 400),
    tags: it.tags || [],
    network: a.network || null,
    priceUsd: a.maxAmountRequired ? Number(a.maxAmountRequired) / 1e6 : null,
    calls30d: q.l30DaysTotalCalls || 0,
    payers30d: q.l30DaysUniquePayers || 0,
    lastCalledAt: q.lastCalledAt || null,
    _blob: [it.serviceName, it.description, a.description, (it.tags || []).join(' '), it.resource]
             .filter(Boolean).join(' ').toLowerCase()
  };
});
console.log(`catalog loaded: ${CATALOG.length} services`);

// ---- ranking: relevance x proven usage. Unique payers weighted over raw calls,
//      because one bot hammering an endpoint is not the same as broad demand. ----
function rank(q, limit = 10, network = null) {
  const terms = q.toLowerCase().split(/\s+/).filter(Boolean);
  const scored = [];
  for (const s of CATALOG) {
    if (network && s.network !== network) continue;
    let rel = 0;
    for (const t of terms) {
      if (!s._blob.includes(t)) continue;
      rel += 1;
      if ((s.name || '').toLowerCase().includes(t)) rel += 2;
      if (s.tags.some(g => g.toLowerCase().includes(t))) rel += 1.5;
    }
    if (!rel) continue;
    const trust = Math.log10(1 + s.payers30d) * 2 + Math.log10(1 + s.calls30d);
    scored.push({ ...s, _score: +(rel * (1 + trust)).toFixed(3) });
  }
  scored.sort((a, b) => b._score - a._score);
  return scored.slice(0, limit).map(({ _blob, ...r }) => r);
}

const app = express();

// ---- FREE: lets an agent inspect capability and price before paying ----
app.get('/', (_req, res) => res.json({
  service: 'x402 Bazaar Rank',
  description: 'Search 14k+ x402 services ranked by REAL 30-day usage and unique payers. '
             + 'The official index is unranked; this returns the ones agents actually pay for.',
  paid_endpoint: '/search?q=<capability>&limit=10&network=eip155:8453',
  price_usdc: Number(PRICE) / 1e6,
  network: 'eip155:8453 (Base)',
  catalog_size: CATALOG.length,
  pricing: [
    { endpoint: '/search',  usdc: 0.01, what: 'ranked service search by capability' },
    { endpoint: '/report',  usdc: 0.10,  what: 'full market report: demand, pricing bands, movers' },
    { endpoint: '/alpha',   usdc: 0.50,  what: 'underserved niches: demand-per-provider ranking' },
    { endpoint: '/dataset', usdc: 1.25,  what: 'complete dataset export, all services + metrics' }
  ],
  free_endpoints: ['/', '/health', '/sample']
}));
app.get('/health', (_req, res) => res.json({ ok: true, catalog: CATALOG.length }));
app.get('/sample', (_req, res) => res.json({ note: 'free sample, 3 results', results: rank('search', 3) }));

// ---- PAID ----
const pay = createPaymentMiddleware({
  getRequirements: () => ({
    scheme: 'exact',
    network: 'base',
    maxAmountRequired: PRICE,
    asset: USDC_BASE,
    payTo: PAY_TO,
    description: 'Ranked x402 service discovery: search 14k+ services by capability, '
               + 'ranked by verified 30-day call volume and unique payer count.',
    mimeType: 'application/json'
  })
});

// ---- ПЛАТНЫЕ ТАРИФЫ: не только дешёвый поиск ----
// Цены обоснованы ценностью, а не желанием. $0.001 - это дно рынка (медиана),
// но у нас есть то, чего нет у конкурентов: ПОЛНЫЙ краул с метриками использования.
function tier(amount, description) {
  return createPaymentMiddleware({
    getRequirements: () => ({
      scheme: 'exact', network: 'base', maxAmountRequired: String(amount),
      asset: USDC_BASE, payTo: PAY_TO, description, mimeType: 'application/json'
    })
  });
}

function categoryStats() {
  const stat = {};
  for (const s of CATALOG) {
    for (const t of (s.tags || [])) {
      const d = stat[t] || (stat[t] = { n: 0, payers: 0, calls: 0, prices: [] });
      d.n++; d.payers += s.payers30d; d.calls += s.calls30d;
      if (s.priceUsd != null) d.prices.push(s.priceUsd);
    }
  }
  return stat;
}

// $0.05 — полный отчёт по рынку
app.get('/report', tier(100000,
  'Full x402 market report: category demand, pricing bands, top movers and quiet services. '
  + 'Built from a complete crawl of every listed service with 30-day usage metrics.'),
  (_req, res) => {
    const stat = categoryStats();
    const cats = Object.entries(stat)
      .filter(([, d]) => d.n >= 3)
      .map(([tag, d]) => ({
        tag, providers: d.n, payers30d: d.payers, calls30d: d.calls,
        medianPriceUsd: d.prices.length
          ? d.prices.sort((a, b) => a - b)[Math.floor(d.prices.length / 2)] : null
      }))
      .sort((a, b) => b.payers30d - a.payers30d).slice(0, 40);
    const top = [...CATALOG].sort((a, b) => b.calls30d - a.calls30d).slice(0, 25)
      .map(({ _blob, ...r }) => r);
    res.json({ generatedAt: new Date().toISOString(), catalogSize: CATALOG.length,
               categories: cats, topServices: top });
  });

// $0.25 — где спрос выше конкуренции (то, за что реально платят консультантам)
app.get('/alpha', tier(500000,
  'Underserved-niche finder: categories ranked by demand-per-provider (unique payers divided by '
  + 'number of providers). Shows where paying demand exceeds supply, with price bands.'),
  (_req, res) => {
    const stat = categoryStats();
    const gaps = Object.entries(stat)
      .filter(([, d]) => d.n >= 3 && d.payers >= 10)
      .map(([tag, d]) => ({
        tag, providers: d.n, payers30d: d.payers,
        demandPerProvider: +(d.payers / d.n).toFixed(2),
        medianPriceUsd: d.prices.length
          ? d.prices.sort((a, b) => a - b)[Math.floor(d.prices.length / 2)] : null
      }))
      .sort((a, b) => b.demandPerProvider - a.demandPerProvider).slice(0, 30);
    res.json({ generatedAt: new Date().toISOString(),
               method: 'unique payers per provider, 30d window; min 3 providers and 10 payers',
               opportunities: gaps });
  });

// $0.50 — весь датасет целиком
app.get('/dataset', tier(1250000,
  'Complete x402 service dataset: every indexed service with pricing, network, tags and '
  + '30-day call and unique-payer counts. One-shot export, JSON.'),
  (_req, res) => res.json({ generatedAt: new Date().toISOString(), count: CATALOG.length,
                            services: CATALOG.map(({ _blob, ...r }) => r) }));

app.get('/search', pay, (req, res) => {
  const q = (req.query.q || '').toString().trim();
  if (!q) return res.status(400).json({ error: 'q required' });
  const limit = Math.min(parseInt(req.query.limit) || 10, 50);
  res.json({ query: q, results: rank(q, limit, req.query.network || null) });
});

// ---------------- EMAIL TRACK: our own opt-in, our own consent proof ----------------
const EO_LIST = '5aafce28-ad18-11f1-9ced-1760a9b2e09e';
const EO_KEY  = env.EMAILOCTOPUS_API_KEY;
import crypto from 'node:crypto';

async function eo(method, p, body) {
  const r = await fetch('https://api.emailoctopus.com' + p, {
    method,
    headers: { Authorization: 'Bearer ' + EO_KEY, Accept: 'application/json',
               'Content-Type': 'application/json', 'User-Agent': 'P0-agent/0.1' },
    body: body ? JSON.stringify(body) : undefined
  });
  let j = {}; try { j = await r.json(); } catch {}
  return { status: r.status, body: j };
}

app.use(express.json());
app.use(express.urlencoded({ extended: true }));

app.get('/join', (_req, res) => res.sendFile(path.join(__dirname, 'join.html')));

app.post('/subscribe', async (req, res) => {
  const email = String(req.body.email || '').trim().toLowerCase();
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email))
    return res.status(400).json({ ok: false, error: 'valid email required' });
  const token = crypto.randomBytes(16).toString('hex');
  const proof = JSON.stringify({
    ts: new Date().toISOString(),
    ip: (req.headers['x-forwarded-for'] || req.socket.remoteAddress || '').toString().split(',')[0],
    ua: (req.headers['user-agent'] || '').slice(0, 180),
    page: '/join', token
  });
  // our DB is the asset; the ESP is a pipe
  try {
    const db = new DatabaseSync(DB);
    db.prepare(`INSERT OR IGNORE INTO subscribers(email,status,source,consent_proof,segment,created_at)
                VALUES (?,?,?,?,?,?)`)
      .run(email, 'pending', 'x402-service', proof, 'founding', new Date().toISOString());
    db.close();
  } catch (e) { return res.status(500).json({ ok: false, error: 'store failed' }); }
  // pending in the ESP too - nobody is mailed until they confirm
  const r = await eo('POST', `/lists/${EO_LIST}/contacts`,
    { email_address: email, status: 'pending', tags: ['source:x402-service', 'cohort:founding'] });
  res.json({ ok: true, status: 'pending',
             confirm: `/confirm?t=${token}`,
             note: 'Confirm to complete signup. Nothing is sent before you confirm.',
             esp: r.status });
});

app.get('/confirm', (req, res) => {
  const t = String(req.query.t || '');
  const db = new DatabaseSync(DB);
  const row = db.prepare(`SELECT id,email FROM subscribers WHERE consent_proof LIKE ? AND status='pending'`)
                .get('%' + t + '%');
  if (!row) { db.close(); return res.status(404).send('<h2>Link not valid or already confirmed.</h2>'); }
  db.prepare(`UPDATE subscribers SET status='confirmed' WHERE id=?`).run(row.id);
  db.close();
  eo('PUT', `/lists/${EO_LIST}/contacts/` + crypto.createHash('md5').update(row.email).digest('hex'),
     { status: 'subscribed' }).catch(() => {});
  res.send(`<body style="font:16px system-ui;background:#05070d;color:#dfe8fb;padding:48px">
    <h2 style="color:#37d99a">Confirmed.</h2><p>${row.email} is on the list.</p>
    <p style="color:#7d8db0">Weekly x402 market intelligence, from a full crawl of 14,231 services.</p></body>`);
});

// ---------------- LIVE AGENT DASHBOARD ----------------
import { DatabaseSync } from 'node:sqlite';
const DB = path.join(ROOT, 'data', 'brain.db');

function agentState() {
  const db = new DatabaseSync(DB, { readOnly: true });
  const one = (q, ...a) => { try { return db.prepare(q).get(...a); } catch { return {}; } };
  const all = (q, ...a) => { try { return db.prepare(q).all(...a); } catch { return []; } };
  const n = (q, ...a) => (one(q, ...a) || {}).c || 0;

  const agents = [
    { id: 'scout',        role: 'Разведчик',        job: 'ищет данные и подшивает источники',
      work: n(`SELECT COUNT(*) c FROM evidence WHERE agent='scout'`) + n(`SELECT COUNT(*) c FROM sources`),
      last: (one(`SELECT MAX(created_at) t FROM evidence WHERE agent='scout'`) || {}).t },
    { id: 'proposer',        role: 'Предлагающий',        job: 'вносит предложения с фальсификатором',
      work: n(`SELECT COUNT(*) c FROM proposals`),
      last: (one(`SELECT MAX(created_at) t FROM proposals`) || {}).t },
    { id: 'verifier',        role: 'Проверяющий',        job: 'независимо перепроверяет факты',
      work: n(`SELECT COUNT(*) c FROM evidence WHERE agent='frontier-escalation'`),
      last: (one(`SELECT MAX(created_at) t FROM evidence WHERE agent='frontier-escalation'`) || {}).t },
    { id: 'adversary',        role: 'Оппонент',        job: 'пытается убить предложение',
      work: n(`SELECT COUNT(*) c FROM objections`),
      last: (one(`SELECT MAX(created_at) t FROM objections`) || {}).t },
    { id: 'judge',        role: 'Судья',        job: 'решает, разобрав сильнейший довод',
      work: n(`SELECT COUNT(*) c FROM rulings`),
      last: (one(`SELECT MAX(created_at) t FROM rulings`) || {}).t },
    { id: 'orchestrator', role: 'Оркестратор', job: 'маршрутизация, гейты, журнал',
      work: n(`SELECT COUNT(*) c FROM messages`),
      last: (one(`SELECT MAX(created_at) t FROM messages`) || {}).t },
    { id: 'explorer',  role: 'Исследователь', job: 'ищет свободные ниши и другие пути',
      work: n(`SELECT COUNT(*) c FROM evidence WHERE agent='explorer'`),
      last: (one(`SELECT MAX(created_at) t FROM evidence WHERE agent='explorer'`) || {}).t },
    { id: 'critic',    role: 'Критик', job: 'проверяет работу остальных агентов',
      work: n(`SELECT COUNT(*) c FROM evidence WHERE agent='critic'`),
      last: (one(`SELECT MAX(created_at) t FROM evidence WHERE agent='critic'`) || {}).t },
    { id: 'optimizer', role: 'Оптимизатор', job: 'измеряет систему и улучшает её',
      work: n(`SELECT COUNT(*) c FROM evidence WHERE agent='optimizer'`),
      last: (one(`SELECT MAX(created_at) t FROM evidence WHERE agent='optimizer'`) || {}).t },
    { id: 'merchant', role: 'Коммерсант', job: 'цены и тарифы по рынку',
      work: n(`SELECT COUNT(*) c FROM evidence WHERE agent='merchant'`),
      last: (one(`SELECT MAX(created_at) t FROM evidence WHERE agent='merchant'`) || {}).t },
    { id: 'distributor', role: 'Дистрибьютор', job: 'обнаружимость и каналы',
      work: n(`SELECT COUNT(*) c FROM evidence WHERE agent='distributor'`),
      last: (one(`SELECT MAX(created_at) t FROM evidence WHERE agent='distributor'`) || {}).t },
    { id: 'scribe', role: 'Писарь', job: 'отчёты и тексты из данных',
      work: n(`SELECT COUNT(*) c FROM evidence WHERE agent='scribe'`),
      last: (one(`SELECT MAX(created_at) t FROM evidence WHERE agent='scribe'`) || {}).t },
    { id: 'leads', role: 'Разведка клиентов', job: 'находит компании, которые уже платят',
      work: n(`SELECT COUNT(*) c FROM leads`),
      last: (one(`SELECT MAX(found_at) t FROM leads`) || {}).t },
    { id: 'salesman', role: 'Продавец', job: 'ставит диагноз клиенту и готовит предложение',
      work: n(`SELECT COUNT(*) c FROM lead_problems`),
      last: (one(`SELECT MAX(found_at) t FROM lead_problems`) || {}).t },
    { id: 'postman', role: 'Почтальон', job: 'подписчики, теги, запуск серии писем',
      work: n(`SELECT COUNT(*) c FROM subscribers`) + n(`SELECT COUNT(*) c FROM email_events`),
      last: (one(`SELECT MAX(created_at) t FROM messages WHERE sender='postman'`) || {}).t },
    { id: 'craftsman', role: 'Мастеровой', job: 'сверяет утверждения с кодом и стережёт PR',
      work: n(`SELECT COUNT(*) c FROM claim_checks`) + n(`SELECT COUNT(*) c FROM pull_requests`),
      last: (one(`SELECT MAX(checked_at) t FROM claim_checks`) || {}).t },
    { id: 'bounty', role: 'Охотник за баунти', job: 'ищет оплачиваемые задачи в открытых репозиториях',
      work: n(`SELECT COUNT(*) c FROM bounties`),
      last: (one(`SELECT MAX(found_at) t FROM bounties`) || {}).t },
    { id: 'mechanic', role: 'Механик', job: 'чинит код, откатывает при провале аудита',
      work: n(`SELECT COUNT(*) c FROM code_fixes`),
      last: (one(`SELECT MAX(at) t FROM code_fixes`) || {}).t },
    { id: 'browser_scout', role: 'Браузерный разведчик', job: 'читает площадки, которые рисуются скриптом и не видны обычному запросу',
      work: n(`SELECT COUNT(*) c FROM agent_decisions WHERE agent='browser_scout'`),
      last: (one(`SELECT MAX(decided_at) t FROM agent_decisions WHERE agent='browser_scout'`) || {}).t },
    { id: 'prospector', role: 'Разведчик заработка', job: 'ищет ВСЕ способы заработать и щупает их о наши стены',
      work: n(`SELECT COUNT(*) c FROM money_paths`),
      last: (one(`SELECT MAX(checked_at) t FROM money_paths`) || {}).t },
    { id: 'improver', role: 'Улучшатель', job: 'правит код других агентов языковой моделью, откатывая всё, что ухудшает аудит',
      work: n(`SELECT COUNT(*) c FROM code_fixes WHERE outcome LIKE '%моделью%'`),
      last: (one(`SELECT MAX(at) t FROM code_fixes`) || {}).t },
    { id: 'executor', role: 'Исполнитель', job: 'делает работу: извлекает интерфейс проекта разбором кода и сверяет каждое утверждение с исходником',
      work: n(`SELECT COUNT(*) c FROM evidence WHERE agent='executor'`),
      last: (one(`SELECT MAX(created_at) t FROM evidence WHERE agent='executor'`) || {}).t },
    { id: 'supplier', role: 'Снабженец', job: 'держит перекличку бесплатных источников: что живо, что умерло и почему',
      work: n(`SELECT COUNT(*) c FROM agent_decisions WHERE agent='supplier'`),
      last: (one(`SELECT MAX(decided_at) t FROM agent_decisions WHERE agent='supplier'`) || {}).t },
    { id: 'watchdog', role: 'Сторож', job: 'следит за живостью агентов',
      work: n(`SELECT COUNT(*) c FROM evidence WHERE agent='watchdog'`),
      last: (one(`SELECT MAX(created_at) t FROM evidence WHERE agent='watchdog'`) || {}).t }
  ];
  const state = {
    agents,
    // Если каталога рынка нет, об этом надо СКАЗАТЬ. Иначе пустая выдача
    // поиска выглядит как пустой рынок, а это разные вещи.
    catalog: { size: CATALOG.length, note: catalogNote },
    mission: {
      payments:  n(`SELECT COUNT(*) c FROM payments`),
      spend:     n(`SELECT COUNT(*) c FROM spend`),
      human:     n(`SELECT COUNT(*) c FROM human_interventions`),
      evidence:  n(`SELECT COUNT(*) c FROM evidence`),
      sources:   n(`SELECT COUNT(*) c FROM sources`),
      blocking:  n(`SELECT COUNT(*) c FROM objections WHERE severity='blocking'`),
      asked:     n(`SELECT COUNT(*) c FROM messages WHERE topic='ask'`),
      answered:  n(`SELECT COUNT(*) c FROM messages WHERE topic='answer'`),
      handoffs:  n(`SELECT COUNT(*) c FROM messages WHERE topic='handoff'`)
    },
    service: { live: true, catalog: CATALOG.length, priceUsd: Number(PRICE)/1e6, payTo: PAY_TO },
    feed: all(`SELECT 'evidence' k, id, substr(claim,1,150) t, created_at FROM evidence
               UNION ALL SELECT 'objection', id, substr(argument,1,150), created_at FROM objections
               UNION ALL SELECT 'proposal', id, substr(summary,1,150), created_at FROM proposals
               ORDER BY created_at DESC LIMIT 14`)
  };
  db.close();
  return state;
}

app.get('/api/status', (_req, res) => { try { res.json(agentState()); }
  catch (e) { res.status(500).json({ error: String(e) }); } });

// ---- WHAT NEEDS THE HUMAN: the actionable queue ----
app.get('/api/queue', (_req, res) => {
  const db = new DatabaseSync(DB, { readOnly: true });
  const all = (q, ...a) => { try { return db.prepare(q).all(...a); } catch { return []; } };
  const out = {
    interventions: all(`SELECT id,what,why,category,agent_could_have,occurred_at
                        FROM human_interventions ORDER BY id DESC`),
    open_proposals: all(`SELECT p.id,p.summary,p.falsifier,p.action_class,p.status,p.created_at,
                          (SELECT COUNT(*) FROM objections o WHERE o.proposal_id=p.id
                             AND o.severity='blocking') blocking
                         FROM proposals p WHERE p.status IN ('proposed','approved') ORDER BY p.id DESC`),
    blocking: all(`SELECT o.id,o.proposal_id,o.argument,o.created_at
                   FROM objections o WHERE o.severity='blocking' ORDER BY o.id DESC`),
    // ЭСКАЛАЦИИ. Механизм существовал, но за всё время не был вызван ни разу
    // и нигде не показывался: суждения, которые локальной модели запрещены,
    // уходили в никуда. Теперь они видны здесь и ждут ответа.
    escalations: all(`SELECT id,sender,body,created_at FROM messages
                      WHERE recipient='ESCALATION' AND consumed_at IS NULL
                      ORDER BY id DESC LIMIT 20`),
    subscribers: all(`SELECT id,email,status,created_at FROM subscribers ORDER BY id DESC LIMIT 20`)
  };
  db.close(); res.json(out);
});

// ---- КОНВЕЙЕР ИСПОЛНЕНИЯ: задачи, доказательства, блокеры ----
app.get('/api/execution', (_req, res) => {
  const db = new DatabaseSync(DB, { readOnly: true });
  const all = (q, ...a) => { try { return db.prepare(q).all(...a); } catch { return []; } };
  const n = (q) => { try { return db.prepare(q).get().c || 0; } catch { return 0; } };

  const states = ['queued', 'running', 'done', 'failed', 'blocked', 'cancelled'];
  const board = {};
  for (const st of states) n0(st);
  function n0(st) { board[st] = n(`SELECT COUNT(*) c FROM tasks WHERE state='${st}'`); }

  const out = {
    board,
    with_proof: n(`SELECT COUNT(DISTINCT task_id) c FROM proof_of_work`),
    tasks: all(`SELECT id,objective,next_action,owner_agent,state,money_proximity,attempts,
                blocker_kind,blocker_detail,updated_at,
                (SELECT COUNT(*) FROM proof_of_work p WHERE p.task_id=tasks.id) proofs
                FROM tasks ORDER BY
                CASE state WHEN 'running' THEN 0 WHEN 'queued' THEN 1 WHEN 'blocked' THEN 2
                           WHEN 'failed' THEN 3 ELSE 4 END,
                money_proximity ASC, id DESC LIMIT 40`),
    proofs: all(`SELECT task_id,kind,reference,detail,created_at FROM proof_of_work
                 ORDER BY id DESC LIMIT 20`),
    leads: all(`SELECT domain,services,payers_30d,spend_signal,top_tags FROM leads
                ORDER BY spend_signal DESC LIMIT 15`),
    problems: all(`SELECT domain,problem,evidence,service_offer,price_usd FROM lead_problems
                   ORDER BY severity DESC LIMIT 20`),
    // ТОЛЬКО ДОСТУПНЫЕ. Суммировать все подряд — значит показывать деньгами
    // задачи, которые уже выплачены другим или разобраны толпой. Поймано на
    // tscircuit#92: $75 в базе, а на деле 72 заявки и премия уже выдана.
    bounties: all(`SELECT repo,title,amount_usd,stars,language,url,rivals FROM bounties
                   WHERE status='found' ORDER BY fit_score DESC LIMIT 10`),
    bounty_value: (() => { try {
      return db.prepare(`SELECT COALESCE(SUM(amount_usd),0) c FROM bounties
                         WHERE status='found'`).get().c; }
      catch { return 0; } })(),
    // МАТРИЦА ГОТОВНОСТИ. Показывает не «сколько агентов», а сколько звеньев
    // цепочки от нуля до выручки реально работают — проверенных вызовом.
    matrix: all(`SELECT stage,owner_agent,verdict,detail,blocker FROM capability_matrix
                 ORDER BY ord`),
    matrix_ok: (() => { try {
      return db.prepare(`SELECT COUNT(*) c FROM capability_matrix
                         WHERE verdict='РАБОТАЕТ'`).get().c; } catch { return null; } })(),
    money_paths: all(`SELECT platform,category,payout,score,wall,open_to_us
                      FROM money_paths ORDER BY open_to_us DESC, score DESC LIMIT 12`),
    path_stats: (() => { try {
      const r = db.prepare(`SELECT COUNT(*) total,
        SUM(CASE WHEN open_to_us=1 THEN 1 ELSE 0 END) open,
        SUM(CASE WHEN open_to_us=0 THEN 1 ELSE 0 END) closed FROM money_paths`).get();
      const blind = db.prepare(`SELECT COUNT(*) c FROM path_categories
                                WHERE searched_at IS NULL`).get().c;
      const cats = db.prepare(`SELECT COUNT(*) c FROM path_categories`).get().c;
      return { total: r.total, open: r.open||0, closed: r.closed||0,
               blind_categories: blind, categories: cats }; }
      catch { return null; } })(),
    fixes: all(`SELECT file,problem,outcome,detail,at FROM code_fixes
                ORDER BY id DESC LIMIT 8`),
    bounty_dropped: (() => { try {
      return db.prepare(`SELECT COUNT(*) c FROM bounties WHERE status='lost'`).get().c; }
      catch { return 0; } })(),
    pipeline_value: (() => { try {
      return db.prepare(`SELECT COALESCE(SUM(price_usd),0) c FROM lead_problems`).get().c; }
      catch { return 0; } })(),
    external: {
      known: n(`SELECT COUNT(*) c FROM external_agents`),
      messages: n(`SELECT COUNT(*) c FROM external_messages`),
      blocked_injection: n(`SELECT COUNT(*) c FROM external_messages WHERE verdict='rejected_injection'`),
    },
  };
  db.close();
  res.json(out);
});

// ---- ЭКОНОМИКА: стадия, сводка, выживание, скоринг ----
app.get('/api/economics', (_req, res) => {
  const db = new DatabaseSync(DB, { readOnly: true });
  const all = (q, ...a) => { try { return db.prepare(q).all(...a); } catch { return []; } };
  const one = (q, ...a) => { try { return db.prepare(q).get(...a) || {}; } catch { return {}; } };
  const n = (q) => (one(q).c || 0);

  const revenue = (one(`SELECT COALESCE(SUM(CAST(amount AS REAL)),0) c FROM payments`).c) || 0;
  const spend = n(`SELECT COUNT(*) c FROM spend`);
  const stages = [[0,'СТАДИЯ 0 — доказательство','выручки нет; цель: первый сторонний платёж'],
                  [0.01,'СТАДИЯ 1 — первая выручка','деньги пришли; цель: повторить'],
                  [100,'СТАДИЯ 2 — повторяемость','цель: стабильный поток'],
                  [1000,'СТАДИЯ 3 — самоокупаемость','система платит за себя']];
  let stage = stages[0];
  for (const st of stages) if (revenue >= st[0]) stage = st;

  // профильный выход каждого агента: у разных агентов он разный
  const outputs = {
    adversary: n(`SELECT COUNT(*) c FROM objections`),
    judge: n(`SELECT COUNT(*) c FROM rulings`),
    proposer: n(`SELECT COUNT(*) c FROM proposals`),
  };
  const runs = all(`SELECT agent, COUNT(*) runs,
                    SUM(CASE WHEN status='ok' THEN 1 ELSE 0 END) ok FROM runs GROUP BY agent`);
  const silenceOk = new Set(['watchdog','critic','adversary']);
  const survival = runs.map(r => {
    const out = outputs[r.agent] !== undefined ? outputs[r.agent]
      : n(`SELECT COUNT(*) c FROM evidence WHERE agent='${r.agent.replace(/'/g,"")}'`);
    let verdict = 'оставить';
    if (silenceOk.has(r.agent) && out === 0) verdict = 'оставить: его продукт — отсутствие проблем';
    else if (r.runs >= 10 && out === 0) verdict = 'кандидат на паузу';
    else if (r.runs >= 5 && r.ok / r.runs < 0.5) verdict = 'чинить';
    return { agent: r.agent, runs: r.runs, output: out,
             rate: r.runs ? Math.round(100 * r.ok / r.runs) : 0, verdict };
  }).sort((a, b) => b.output - a.output);

  const day = new Date(Date.now() - 864e5).toISOString();
  res.json({
    stage: stage[1], goal: stage[2],
    revenue_usd: revenue, spend_rows: spend, zero_capital_intact: spend === 0,
    runs_24h: n(`SELECT COUNT(*) c FROM runs WHERE started_at > '${day}'`),
    failures_24h: n(`SELECT COUNT(*) c FROM runs WHERE status='error' AND started_at > '${day}'`),
    findings_24h: n(`SELECT COUNT(*) c FROM evidence WHERE created_at > '${day}'`),
    survival,
    opportunities: all(`SELECT name, score, verdict, gate_notes FROM opportunities
                        ORDER BY score DESC`)
  });
});

// ---- ПУЛЬС: что агенты делают ПРЯМО СЕЙЧАС ----
// Отдельно от чата: чат дедуплицируется и потому молчит, когда нового нет,
// а человек читает тишину как «система умерла». Здесь идёт сырой поток
// прогонов — он всегда свежий, потому что это не выводы, а пульс.
app.get('/api/pulse', (_req, res) => {
  const db = new DatabaseSync(DB, { readOnly: true });
  let runs = [];
  try {
    runs = db.prepare(`SELECT agent,status,notes,started_at FROM runs
                       ORDER BY id DESC LIMIT 8`).all();
  } catch {}
  db.close();
  res.json({ runs });
});

// ---- СИГНАЛЫ: настоящие сообщения между агентами ----
// Дашборд рисует импульс на КАЖДОЕ реальное сообщение. Нет общения — нет импульсов.
app.get('/api/signals', (req, res) => {
  const since = Number(req.query.since || 0);
  const db = new DatabaseSync(DB, { readOnly: true });
  let rows = [];
  try {
    rows = db.prepare(`SELECT id,sender,recipient,topic,substr(body,1,120) body,created_at
                       FROM messages WHERE id > ? AND topic IN ('ask','answer','handoff','chat')
                       ORDER BY id LIMIT 60`).all(since);
  } catch {}
  let maxId = since;
  try { maxId = db.prepare(`SELECT COALESCE(MAX(id),0) c FROM messages`).get().c; } catch {}
  db.close();
  res.json({ signals: rows, maxId });
});

// ---- AGENT CHAT (russian) ----
// НАБЛЮДАЕМОСТЬ В ФОРМАТЕ PROMETHEUS. Обычный текст, без зависимостей: если
// владелец поднимет Grafana, она подключится сюда как есть и менять ничего не
// придётся. Пока не поднял — замеры всё равно работают, и их читают агенты.
app.get('/metrics', (_req, res) => {
  const db = new DatabaseSync(DB, { readOnly: true });
  let rows = [];
  try {
    rows = db.prepare(
      "SELECT kind, name, COUNT(*) c, SUM(1-ok) bad, AVG(ms) avg FROM spans " +
      // Метка хранится с T и смещением, а datetime('now') отдаёт её с пробелом:
      // сравнение строк тогда истинно всегда, и «за сутки» означает «за всё
      // время». strftime даёт тот же формат, в котором метка записана.
      "WHERE at > strftime('%Y-%m-%dT%H:%M:%S','now','-24 hours') " +
      "GROUP BY kind, name").all();
  } catch { /* таблицы ещё нет — цикл не проходил */ }
  db.close();

  // Метка Prometheus не терпит кавычек и переносов: они ломают разбор у
  // сборщика, и метрика молча пропадает вместо того, чтобы быть неверной.
  const esc = (v) => String(v).replace(/["\\\n]/g, '_');
  const out = [];
  const block = (metric, help, type, pick) => {
    out.push('# HELP ' + metric + ' ' + help, '# TYPE ' + metric + ' ' + type);
    rows.forEach((r) => {
      out.push(metric + '{kind="' + esc(r.kind) + '",name="' + esc(r.name) + '"} ' + pick(r));
    });
  };
  block('p0_span_calls_total', 'Отрезков работы за сутки', 'counter', (r) => r.c);
  block('p0_span_failed_total', 'Сколько из них упало', 'counter', (r) => r.bad || 0);
  block('p0_span_avg_ms', 'Среднее время отрезка, миллисекунды', 'gauge',
        (r) => Math.round(r.avg || 0));
  res.type('text/plain; version=0.0.4').send(out.join('\n') + '\n');
});

app.get('/api/chat', (_req, res) => {
  const db = new DatabaseSync(DB, { readOnly: true });
  let rows = [];
  try {
    // АДРЕСАТ ОБЯЗАТЕЛЕН В ВЫДАЧЕ. Без него вопрос, ответ и передача работы
    // выглядят на дашборде так же, как объявление в пустоту: 275 передач и 72
    // пары «вопрос-ответ» были неотличимы от болтовни. Общение, из которого
    // убрали второго участника, перестаёт быть общением.
    rows = db.prepare(`SELECT id,sender,recipient,topic,body,created_at FROM messages
                       WHERE topic IN ('chat','ask','answer','handoff') ORDER BY id DESC LIMIT 60`).all();
  } catch {}
  // ИТОГИ ЗА ВСЁ ВРЕМЯ, А НЕ ТОЛЬКО ЗА ОКНО. В последние шестьдесят строк
  // попадают почти одни объявления, и по ним кажется, будто агенты друг с
  // другом не разговаривают вовсе. Между тем пар «вопрос-ответ» набралось
  // семьдесят две — просто они старше окна. Показывать надо оба числа:
  // одно окно без истории вводит в заблуждение ровно так же, как одна
  // история без окна.
  let totals = {};
  try {
    for (const r of db.prepare(
        "SELECT topic, COUNT(*) c FROM messages GROUP BY topic").all()) {
      totals[r.topic] = r.c;
    }
  } catch { /* таблицы может не быть в свежей облачной базе */ }
  db.close();
  res.json({ messages: rows.reverse(), totals });
});

// ---- ACTIONS the owner can take from the dashboard ----
// ОТВЕТ НА ЭСКАЛАЦИЮ. Суждения уходили в таблицу, и разрешить их было нечем:
// resolve_escalation() существовал без единого вызывающего. Теперь у очереди
// суждений есть выход, иначе она копится молча и выглядит как «решать нечего».
app.post('/api/resolve', (req, res) => {
  const { escalation_id, answer } = req.body || {};
  if (!escalation_id || !answer || !String(answer).trim())
    return res.status(400).json({ ok:false, error:'нужны escalation_id и непустой answer' });
  const db = new DatabaseSync(DB);
  try {
    const row = db.prepare(`SELECT id FROM messages WHERE id=? AND recipient='ESCALATION'
                            AND consumed_at IS NULL`).get(escalation_id);
    if (!row) { db.close(); return res.status(404).json({ ok:false, error:'нет такой открытой эскалации' }); }
    const t = new Date().toISOString();
    db.prepare(`UPDATE messages SET consumed_at=? WHERE id=?`).run(t, escalation_id);
    db.prepare(`INSERT INTO messages(sender,recipient,topic,body,created_at)
                VALUES (?,?,?,?,?)`).run('owner', 'orchestrator', 'resolution', String(answer), t);
    db.close();
    res.json({ ok:true, escalation_id });
  } catch (e) { db.close(); res.status(500).json({ ok:false, error:String(e).slice(0,200) }); }
});

app.post('/api/decide', (req, res) => {
  const { proposal_id, decision, note } = req.body || {};
  if (!['approve','reject','defer'].includes(decision))
    return res.status(400).json({ ok:false, error:'decision must be approve|reject|defer' });
  const db = new DatabaseSync(DB);
  const now = new Date().toISOString();
  db.prepare(`INSERT INTO rulings(proposal_id,decision,reasoning,model_used,created_at)
              VALUES (?,?,?,?,?)`)
    .run(Number(proposal_id), decision, note || 'owner decision via dashboard', 'owner', now);
  db.prepare(`UPDATE proposals SET status=? WHERE id=?`)
    .run(decision === 'approve' ? 'approved' : decision === 'reject' ? 'rejected' : 'proposed',
         Number(proposal_id));
  db.prepare(`INSERT INTO human_interventions(what,why,category,agent_could_have,occurred_at)
              VALUES (?,?,?,?,?)`)
    .run(`Ruled ${decision} on proposal #${proposal_id}`, note || 'dashboard', 'approval', 0, now);
  db.close(); res.json({ ok:true, proposal_id, decision });
});

app.post('/api/objection/:id/resolve', (req, res) => {
  const db = new DatabaseSync(DB);
  db.prepare(`UPDATE objections SET severity='concern' WHERE id=?`).run(Number(req.params.id));
  db.prepare(`INSERT INTO human_interventions(what,why,category,agent_could_have,occurred_at)
              VALUES (?,?,?,?,?)`)
    .run(`Downgraded blocking objection #${req.params.id}`,
         String((req.body||{}).note || 'owner override'), 'approval', 0, new Date().toISOString());
  db.close(); res.json({ ok:true });
});

// ---- MAKE AGENTS WORK: trigger a real job ----
import { spawn } from 'node:child_process';
const JOBS = {};
app.post('/api/run/:agent', (req, res) => {
  const agent = req.params.agent;
  const q = String((req.body||{}).query || 'x402 paid api pricing');
  const scripts = {
    scout: ['-3.13','-X','utf8','-c',
      `import sys,json; sys.path.insert(0,r'${ROOT}')
from agents import scout
` +
      `r=scout.research_index(${JSON.stringify(q)})
` +
      `print('matches:',r.get('matches'))
` +
      `[print(' ',h['payers30d'],'payers',h['calls30d'],'calls',h['name'][:44]) for h in (r.get('top') or [])]`],
    crawl: ['-3.13','-X','utf8','-c',
      `import sys; sys.path.insert(0,r'${ROOT}')
import urllib.request,json
` +
      `r=urllib.request.urlopen(urllib.request.Request('https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources?limit=100',headers={'User-Agent':'P0','Accept':'application/json'}),timeout=30)
` +
      `d=json.loads(r.read().decode());print('refreshed',len(d.get('items',[])))`]
  };
  if (!scripts[agent]) return res.status(400).json({ ok:false, error:'unknown agent job' });
  const id = Date.now().toString(36);
  const pr = spawn('py', scripts[agent], { cwd: ROOT });
  JOBS[id] = { agent, out:'', done:false };
  pr.stdout.on('data', d => JOBS[id].out += d);
  pr.stderr.on('data', d => JOBS[id].out += d);
  pr.on('close', c => { JOBS[id].done = true; JOBS[id].code = c; });
  res.json({ ok:true, job:id, agent });
});
app.get('/api/job/:id', (req,res) => res.json(JOBS[req.params.id] || { error:'no such job' }));
app.get('/dashboard', (_req, res) => res.sendFile(path.join(__dirname, 'dashboard.html')));

const PORT = process.env.PORT || 8402;
app.listen(PORT, () => console.log(`x402 service on :${PORT} -> payTo ${PAY_TO}`));
