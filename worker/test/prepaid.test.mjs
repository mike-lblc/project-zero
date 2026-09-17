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
                  'import SNAPSHOT from "./snapshot.json" with { type: "json" };');
src = src.slice(0, src.lastIndexOf("export default")) + "export { prepaid, TIERS, INBOX_CACHE, SPENT };\n";
const dir = mkdtempSync(join(tmpdir(), "p0-prepaid-"));
writeFileSync(join(dir, "index.mjs"), src);
for (const f of ["catalog.slim.json", "snapshot.json"])
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

console.log("\n  passed " + pass + ", failed " + fail);
process.exit(fail ? 1 : 0);
