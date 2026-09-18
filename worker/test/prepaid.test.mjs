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
src = src.slice(0, src.lastIndexOf("export default")) + "export { prepaid, TIERS, INBOX_CACHE, SPENT };\n";
const dir = mkdtempSync(join(tmpdir(), "p0-prepaid-"));
writeFileSync(join(dir, "index.mjs"), src);
for (const f of ["catalog.slim.json", "snapshot.json", "routes.json"])
  writeFileSync(join(dir, f), readFileSync(join(root, f)));
const { prepaid, TIERS, INBOX_CACHE, SPENT } = await import(pathToFileURL(join(dir, "index.mjs")).href);

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

console.log("\n  passed " + pass + ", failed " + fail);
process.exit(fail ? 1 : 0);

// ИНДЕКСАТОР ОПАЗДЫВАЕТ — СПРАШИВАЕМ ЦЕПЬ (18.09.2026).
//
// Почему это появилось: скаут каталога расплачивается на своей стороне и ТУТ ЖЕ
// зовёт маршрут. Его двенадцать переводов уложились в 72 секунды, и на все мы
// ответили 402 — потому что искали платёж только в индексаторе Blockscout, а тот
// отстаёт. В записи каталога о нас это и осталось: onchain_volume_usd_30d = 0.117
// при deliveries = 0 и paid_verified = false. В их переписи это отдельный класс
// отказа («ещё 25 взяли платёж и всё равно ответили 402»), и повторных покупок в
// нём не бывает.
//
// Проверено на настоящей цепи: eth_getLogs по окну блоков 51424200..51424300
// находит те самые переводы (0.001/0.001/0.010 USDC от 0x7e571e95…, хеш
// 0x92122bdc… совпадает с первым). Здесь закреплено поведение, а не сеть.
test("свежий платёж виден из цепи, даже когда индексатор о нём ещё молчит", async () => {
  const payTo = "0xECa891e34b3E5873181Fb779672564E198C55354";
  const fresh = new Date().toISOString();
  const saved = globalThis.fetch;
  globalThis.fetch = async (url, opt) => {
    const u = String(url);
    if (u.includes("blockscout")) {
      // индексатор ещё НЕ знает о переводе
      return { ok: true, json: async () => ({ items: [] }) };
    }
    if (u.includes("mainnet.base.org")) {
      const body = JSON.parse(opt.body);
      if (body.method === "eth_blockNumber") {
        return { ok: true, json: async () => ({ result: "0x3100000" }) };
      }
      if (body.method === "eth_getLogs") {
        const addr = String(body.params[0].address).toLowerCase();
        if (addr !== "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913") {
          return { ok: true, json: async () => ({ result: [] }) };
        }
        return { ok: true, json: async () => ({ result: [{
          transactionHash: "0xfeed0000000000000000000000000000000000000000000000000000000000ab",
          topics: [
            "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef",
            "0x0000000000000000000000007e571e959cc7c75ccdd2eac24f8775ea2eaa2f09",
            "0x000000000000000000000000eca891e34b3e5873181fb779672564e198c55354",
          ],
          data: "0x" + (1000).toString(16),          // 0.001 USDC
        }] }) };
      }
    }
    return { ok: false, json: async () => ({}) };
  };
  try {
    const tail = await M.recentTransfersViaRpc(payTo);
    assert.equal(tail.length, 1, "перевод из цепи не найден");
    assert.equal(tail[0].to, payTo.toLowerCase());
    assert.equal(tail[0].from.toLowerCase(), "0x7e571e959cc7c75ccdd2eac24f8775ea2eaa2f09");
    assert.equal(tail[0].raw, "1000", "сумма прочитана неверно");
  } finally {
    globalThis.fetch = saved;
  }
});

test("недоступная цепь не ломает выдачу: остаёмся с индексатором", async () => {
  const saved = globalThis.fetch;
  globalThis.fetch = async () => { throw new Error("rpc down"); };
  try {
    const tail = await M.recentTransfersViaRpc("0xECa891e34b3E5873181Fb779672564E198C55354");
    assert.deepEqual(tail, [], "отказ RPC должен давать пустой хвост, а не исключение");
  } finally {
    globalThis.fetch = saved;
  }
});
