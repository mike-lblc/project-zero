// ПЛАТЁЖ УЖЕ ПРИШЁЛ, ЗАГОЛОВКА НЕТ (17.09.2026).
//
// В 09:35 нам заплатили двенадцать раз, и воркер не записал ни одного paid:
// платёжного заголовка не присылали вовсе. Покупатель перевёл цену прямо на payTo
// и позвал маршрут, а мы ответили 402 — деньги взяли, товар не отдали. Здесь
// закреплены свойства, без которых эта правка опасна: один перевод открывает
// РОВНО один ответ, цент не открывает датасет за четверть доллара, чужой
// получатель и незнакомый токен не считаются, недоступный обозреватель даёт
// отказ, а не исключение.
//
// Запуск: node worker/test/prepaid.test.mjs
import { readFileSync, writeFileSync, mkdtempSync } from "fs";
import { tmpdir } from "os";
import { join, dirname } from "path";
import { fileURLToPath, pathToFileURL } from "url";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..");
let src = readFileSync(join(root, "src", "index.js"), "utf-8");
src = src.replace('import CATALOG from "../catalog.slim.json";',
                  'import CATALOG from "./catalog.slim.json" with { type: "json" };')
         .replace('import SNAPSHOT from "../snapshot.json";',
                  'import SNAPSHOT from "./snapshot.json" with { type: "json" };')
         .replace('import BAKED_ROUTES from "../routes.json";',
                  'import BAKED_ROUTES from "./routes.json" with { type: "json" };');
src = src.slice(0, src.lastIndexOf("export default")) + "export { prepaid, TIERS, INBOX_CACHE, SPENT, directPaid, txViaRpc, recentTransfersViaRpc };\n";
const dir = mkdtempSync(join(tmpdir(), "p0-prepaid-"));
writeFileSync(join(dir, "index.mjs"), src);
for (const f of ["catalog.slim.json", "snapshot.json", "routes.json"])
  writeFileSync(join(dir, f), readFileSync(join(root, f)));
const { prepaid, TIERS, INBOX_CACHE, SPENT, directPaid, txViaRpc } = await import(pathToFileURL(join(dir, "index.mjs")).href);

const PAYTO = "0xECa891e34b3E5873181Fb779672564E198C55354";
const USDC = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913";
const now = Date.now();
const kv = () => { const m = new Map(); return { get: async (k) => m.get(k) ?? null, put: async (k, v) => void m.set(k, v) }; };
const stub = (items) => { INBOX_CACHE.clear(); SPENT.clear(); globalThis.fetch = async () => ({ ok: true, json: async () => ({ items }) }); };
const T = (amt, minsAgo, tx, to = PAYTO, token = USDC) => ({
  transaction_hash: tx, timestamp: new Date(now - minsAgo * 60000).toISOString(),
  to: { hash: to }, from: { hash: "0xPAYER" }, token: { address_hash: token },
  total: { value: String(Math.round(amt * 1e6)) },
});

let pass = 0, fail = 0;
const check = (name, cond, extra = "") => {
  if (cond) { pass++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + " " + extra); }
};

stub([T(0.001, 1, "0xaaa")]);
let store = kv();
let r = await prepaid(TIERS["/networks"], PAYTO, { BOARD: store });
check("fresh payment covering the price is honoured", r.ok && r.tx === "0xaaa", JSON.stringify(r));
check("same transfer cannot be spent twice", !(await prepaid(TIERS["/networks"], PAYTO, { BOARD: store })).ok);

stub([T(0.05, 120, "0xbbb")]);
check("payment older than 30 minutes is refused", !(await prepaid(TIERS["/networks"], PAYTO, { BOARD: kv() })).ok);

stub([T(0.001, 1, "0xccc")]);
check("a cent does not unlock the $0.25 dataset", !(await prepaid(TIERS["/dataset"], PAYTO, { BOARD: kv() })).ok);

stub([T(0.05, 1, "0xddd", "0xSomeoneElse")]);
check("transfer to another address is ignored", !(await prepaid(TIERS["/networks"], PAYTO, { BOARD: kv() })).ok);

stub([T(0.05, 1, "0xeee", PAYTO, "0xdeadbeef")]);
check("unrecognised token is ignored", !(await prepaid(TIERS["/networks"], PAYTO, { BOARD: kv() })).ok);

stub([T(0.05, 1, "0xbig"), T(0.001, 1, "0xsmall"), T(0.02, 1, "0xmid")]);
r = await prepaid(TIERS["/networks"], PAYTO, { BOARD: kv() });
check("the cheapest sufficient payment is consumed first", r.ok && r.tx === "0xsmall", JSON.stringify(r));

INBOX_CACHE.clear(); SPENT.clear(); globalThis.fetch = async () => { throw new Error("net"); };
r = await prepaid(TIERS["/networks"], PAYTO, { BOARD: kv() });
check("explorer outage yields a refusal, not a crash", !r.ok && /unavailable/.test(r.why || ""));


// ЛИМИТ KV ИСЧЕРПАН — заплативший всё равно получает товар.
// 17.09 мы выбили бесплатный предел 1000 записей в сутки, и прежний код на неудачной
// отметке делал continue: покупатель, уже переведший деньги, снова получал 402.
INBOX_CACHE.clear(); SPENT.clear();
stub([T(0.001, 1, "0xkvfail")]);
const failingKv = { get: async () => null, put: async () => { throw new Error("KV 429 put limit"); } };
r = await prepaid(TIERS["/networks"], PAYTO, { BOARD: failingKv });
check("payer is served even when KV writes are blocked", r.ok && r.marked === false, JSON.stringify(r));
check("and that transfer is not served twice in the same isolate",
      !(await prepaid(TIERS["/networks"], PAYTO, { BOARD: failingKv })).ok);

// ПЛАТЁЖ ПРИШЁЛ ПОЗЖЕ, ЧЕМ НАПОЛНИЛСЯ КЭШ.
INBOX_CACHE.clear(); SPENT.clear();
stub([]);
check("empty inbox refuses", !(await prepaid(TIERS["/networks"], PAYTO, { BOARD: kv() })).ok);
globalThis.fetch = async () => ({ ok: true, json: async () => ({ items: [T(0.001, 0, "0xjustnow")] }) });
r = await prepaid(TIERS["/networks"], PAYTO, { BOARD: kv() });
check("a payment made after the cache filled is still found", r.ok && r.tx === "0xjustnow", JSON.stringify(r));

// ИНДЕКСАТОР ОПАЗДЫВАЕТ — СПРАШИВАЕМ ЦЕПЬ (18.09.2026).
//
// Скаут каталога расплачивается на своей стороне и ТУТ ЖЕ зовёт маршрут: его
// двенадцать переводов уложились в 72 секунды, и на все мы ответили 402, потому
// что искали платёж только в индексаторе Blockscout, а тот отстаёт. В записи
// каталога о нас это и осталось: onchain_volume_usd_30d = 0.117 при deliveries = 0
// и paid_verified = false; в их переписи это отдельный класс отказа («ещё 25 взяли
// платёж и всё равно ответили 402»), и повторных покупок в нём не бывает.
//
// Форма запроса проверена на настоящей цепи: eth_getLogs по блокам
// 51424200..51424300 находит те самые переводы (0.001/0.001/0.010 USDC от
// 0x7e571e95…, хеш 0x92122bdc… совпадает с первым). Здесь закреплено поведение.
INBOX_CACHE.clear(); SPENT.clear();
globalThis.fetch = async (url, opt) => {
  const u = String(url);
  if (u.includes("blockscout")) return { ok: true, json: async () => ({ items: [] }) };
  if (u.includes("mainnet.base.org")) {
    const body = JSON.parse(opt.body);
    if (body.method === "eth_blockNumber") return { ok: true, json: async () => ({ result: "0x3100000" }) };
    if (body.method === "eth_getLogs") {
      if (String(body.params[0].address).toLowerCase() !== USDC.toLowerCase())
        return { ok: true, json: async () => ({ result: [] }) };
      return { ok: true, json: async () => ({ result: [{
        transactionHash: "0xfromchain",
        topics: ["0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef",
                 "0x0000000000000000000000007e571e959cc7c75ccdd2eac24f8775ea2eaa2f09",
                 "0x000000000000000000000000" + PAYTO.replace(/^0x/, "").toLowerCase()],
        data: "0x" + (1000).toString(16),
      }] }) };
    }
  }
  return { ok: false, json: async () => ({}) };
};
r = await prepaid(TIERS["/networks"], PAYTO, { BOARD: kv() });
check("a payment the indexer has not seen yet is found on the chain", r.ok && r.tx === "0xfromchain", JSON.stringify(r));

// Недоступная цепь не имеет права ломать выдачу: остаёмся с индексатором.
INBOX_CACHE.clear(); SPENT.clear();
globalThis.fetch = async (url) => {
  if (String(url).includes("mainnet.base.org")) throw new Error("rpc down");
  return { ok: true, json: async () => ({ items: [T(0.001, 1, "0xviaindexer")] }) };
};
r = await prepaid(TIERS["/networks"], PAYTO, { BOARD: kv() });
check("rpc being down still serves from the indexer", r.ok && r.tx === "0xviaindexer", JSON.stringify(r));


// ЗАДУШЕННЫЙ ЛИМИТОМ ИНДЕКСАТОР НЕ ИМЕЕТ ПРАВА ОТКАЗЫВАТЬ ЗАПЛАТИВШЕМУ (18.09.2026).
//
// Найдено на ЖИВОМ сервисе: /dataset?tx=<хеш> на четырёх НАСТОЯЩИХ наших оплатах
// ответил «transaction not found on Base (429)». 429 — это нас придушили лимитом,
// а мы выдавали покупателю «платежа нет». Каталог скаута фиксирует ровно такой
// исход: onchain_volume_usd_30d=0.117 (наши платежи они ВИДЯТ) при deliveries=0.
//
// Отдельный урок про сами тесты: эти проверки сперва были дописаны в КОНЕЦ файла,
// а файл заканчивается process.exit — они не выполнялись ни разу. Вместе с ними
// молча не выполнялись и две вчерашние, которыми я отчитался за проверку цепи.
// Поэтому они здесь, ДО итоговой строки, и в том же стиле check(), что и остальные.
const TOPIC_RL = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef";
const TX_RL = "0x" + "ab".repeat(32);
const pad32rl = (a) => "0x" + a.replace(/^0x/, "").toLowerCase().padStart(64, "0");
const receiptOk = {
  ok: true,
  json: async () => ({ result: { status: "0x1", blockNumber: "0x1",
    logs: [{ address: USDC, topics: [TOPIC_RL, pad32rl(PAYTO), pad32rl(PAYTO)],
             data: "0x" + (250000).toString(16) }] } }),
};
const blockNow = { ok: true, json: async () => ({ result: { timestamp: "0x" + Math.floor(Date.now() / 1000).toString(16) } }) };

// (1) индексатор душит лимитом, цепь отвечает -> товар ОБЯЗАН уйти
SPENT.clear();
globalThis.fetch = async (url, opt) => {
  if (String(url).includes("blockscout")) return { ok: false, status: 429 };
  const m = JSON.parse(opt.body).method;
  if (m === "eth_getTransactionReceipt") return receiptOk;
  if (m === "eth_getBlockByNumber") return blockNow;
  return { ok: false, status: 500 };
};
r = await directPaid(TX_RL, { usd: 0.25 }, PAYTO, { BOARD: kv() });
check("429 индексатора не превращается в «платежа нет»: цепь подтверждает, товар уходит",
      r.ok === true && r.asset === "USDC", JSON.stringify(r));

// (2) молчат ОБА источника -> отказ временный, а не «платежа нет»
SPENT.clear();
globalThis.fetch = async () => { throw new Error("down"); };
r = await directPaid(TX_RL, { usd: 0.25 }, PAYTO, { BOARD: kv() });
check("когда молчат оба источника, отказ временный и не зовётся «не найдено»",
      r.ok === false && r.retryable === true && !/not found/i.test(r.why || ""), JSON.stringify(r));

// (3) цепь ответила «нет такой транзакции» -> честный отказ, повторять нечего
SPENT.clear();
globalThis.fetch = async (url, opt) => {
  if (String(url).includes("blockscout")) return { ok: false, status: 429 };
  if (JSON.parse(opt.body).method === "eth_getTransactionReceipt")
    return { ok: true, json: async () => ({ result: null }) };
  return { ok: false, status: 500 };
};
r = await directPaid(TX_RL, { usd: 0.25 }, PAYTO, { BOARD: kv() });
check("несуществующая транзакция — честный отказ, а не приглашение повторить",
      r.ok === false && !r.retryable, JSON.stringify(r));

// (4) сама txViaRpc обязана различать «цепь молчит» и «транзакции нет»
globalThis.fetch = async () => { throw new Error("down"); };
check("txViaRpc: недоступная цепь = null (неизвестность), а не отсутствие платежа",
      (await txViaRpc(TX_RL, PAYTO)) === null);
globalThis.fetch = async () => ({ ok: true, json: async () => ({ result: null }) });
check("txViaRpc: ответ цепи «нет такой» помечается missing",
      ((await txViaRpc(TX_RL, PAYTO)) || {}).missing === true);

console.log("\n  passed " + pass + ", failed " + fail);
process.exit(fail ? 1 : 0);
