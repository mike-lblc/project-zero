// МАРШРУТ, ОБЪЯВЛЕННЫЙ АГЕНТОМ (17.09.2026).
//
// Владелец разрешил агентам делать всё, что делал человек, включая добавление платных
// маршрутов. Давать агенту писать произвольный JS и деплоить платный сервис — ружьё без
// предохранителя: одна ошибка отдаёт данные бесплатно или роняет выручку в ноль.
// Поэтому маршрут — это ДАННЫЕ: путь, цена, описание и запрос из закрытого списка
// операций, который воркер ИНТЕРПРЕТИРУЕТ. Здесь закреплено, что прислать код нельзя,
// что границы цены held, и что чужой маршрут не переопределить.
//
// Запуск: node worker/test/routes.test.mjs
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
src = src.slice(0, src.lastIndexOf("export default")) +
      "export { validateRouteSpec, runSpec, tiersNow, TIERS, PRICE_FLOOR, PRICE_CAP, ROUTE_CAP };\n";
const dir = mkdtempSync(join(tmpdir(), "p0-routes-"));
writeFileSync(join(dir, "index.mjs"), src);
for (const f of ["catalog.slim.json", "snapshot.json", "routes.json"])
  writeFileSync(join(dir, f), readFileSync(join(root, f)));
const M = await import(pathToFileURL(join(dir, "index.mjs")).href);

let pass = 0, fail = 0;
const check = (name, cond, extra = "") => {
  if (cond) { pass++; console.log("  ok   " + name); }
  else { fail++; console.log("  FAIL " + name + " " + extra); }
};
const good = { path: "/in-agents", usd: 0.001, spec: { source: "catalog", op: "top", by: "payers", where: { tag: "agents" }, limit: 25 },
  what: "Highest-demand x402 services tagged agents, ranked by paying wallets." };
const ok = (r) => M.validateRouteSpec(r)[0];
const why = (r) => M.validateRouteSpec(r)[1];

check("a well-formed declaration is accepted", ok(good), why(good));

// код прислать нельзя — ни как операцию, ни как поле
check("an unknown op is rejected", !ok({ ...good, spec: { ...good.spec, op: "eval" } }));
check("a non-catalog source is rejected", !ok({ ...good, spec: { ...good.spec, source: "http" } }));
check("an unlisted where-field is rejected", !ok({ ...good, spec: { ...good.spec, where: { cmd: "rm -rf" } } }));
check("an unlisted sort field is rejected", !ok({ ...good, spec: { ...good.spec, by: "constructor" } }));
check("a __proto__ sort field is rejected", !ok({ ...good, spec: { ...good.spec, by: "__proto__" } }));
check("a toString sort field is rejected", !ok({ ...good, spec: { ...good.spec, by: "toString" } }));
// Настоящий путь входа — JSON.parse из тела запроса: он СОЗДАЁТ собственный ключ
// "__proto__", в отличие от литерала, где это сеттер прототипа. Проверяем именно его.
const viaJson = JSON.parse(JSON.stringify({ ...good, spec: { ...good.spec } }).replace('"where":{"tag":"agents"}', '"where":{"tag":"agents","__proto__":{"polluted":true}}'));
check("a __proto__ key parsed from the request body is rejected", !ok(viaJson), why(viaJson));
check("and nothing was polluted", ({}).polluted === undefined);
check("a spec that is not an object is rejected", !ok({ ...good, spec: "top" }));

// цена всегда внутри границ воркера
check("a price below the floor is rejected", !ok({ ...good, usd: 0.0000001 }));
check("a price above the cap is rejected", !ok({ ...good, usd: 5 }));
check("a non-numeric price is rejected", !ok({ ...good, usd: "free" }));

// нельзя переопределить скомпилированный маршрут и нельзя странный путь
check("a compiled route cannot be redefined", !ok({ ...good, path: "/search" }),
      "an agent must not be able to hijack /search");
check("a path with slashes is rejected", !ok({ ...good, path: "/a/b" }));
check("an uppercase path is rejected", !ok({ ...good, path: "/Evil" }));
check("an empty description is rejected", !ok({ ...good, what: "short" }));
check("limit above the cap is rejected", !ok({ ...good, spec: { ...good.spec, limit: M.ROUTE_CAP + 1 } }));

// интерпретатор действительно считает по каталогу и уважает предел
const top = M.runSpec({ source: "catalog", op: "top", by: "payers", where: { tag: "agents" }, limit: 3 });
check("interpreting a top spec returns real rows", Array.isArray(top.services) && top.services.length === 3,
      JSON.stringify(top).slice(0, 120));
check("rows carry a resource and payer count", Boolean(top.services[0].resource) && top.services[0].payers30d !== undefined);
const grp = M.runSpec({ source: "catalog", op: "group", by: "network", limit: 5 });
check("interpreting a group spec aggregates", Array.isArray(grp.groups) && grp.groups.length > 0);
const cnt = M.runSpec({ source: "catalog", op: "count", where: { minPayers: 100 } });
check("interpreting a count spec totals", cnt.matched > 0 && cnt.payers30d > 0, JSON.stringify(cnt));
check("a tag that matches nothing yields an empty, not a crash",
      M.runSpec({ source: "catalog", op: "top", by: "payers", where: { tag: "zzz-nope" }, limit: 5 }).services.length === 0);

console.log("\n  passed " + pass + ", failed " + fail);
process.exit(fail ? 1 : 0);
