// ПРИМЕР ОТВЕТА В КАТАЛОГЕ ОБЯЗАН СОВПАДАТЬ С НАСТОЯЩИМ ОТВЕТОМ (17.09.2026).
//
// Индексатор Bazaar показывает пример ответа покупающему агенту ДО оплаты — по нему
// решают, стоит ли платить. Раньше на все одиннадцать маршрутов стояла одна заглушка
// «see tier description»: рядом в каталоге лежат сервисы с конкретными примерами, и
// на их фоне это выглядело как пустая полка.
//
// Теперь примеры конкретные, и появился новый способ соврать: обещать поле, которого
// мы не отдаём. Поэтому здесь каждый ключ примера сверяется с ключами НАСТОЯЩЕГО
// ответа маршрута — и на верхнем уровне, и внутри первого элемента массива.
//
// Запуск: node worker/test/examples.test.mjs
import { readFileSync, writeFileSync, mkdtempSync } from "fs";
import { tmpdir } from "os";
import { join, dirname } from "path";
import { fileURLToPath, pathToFileURL } from "url";
import test from "node:test";
import assert from "node:assert/strict";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..");
let src = readFileSync(join(root, "src", "index.js"), "utf-8");
src = src.replace('import CATALOG from "../catalog.slim.json";',
                  'import CATALOG from "./catalog.slim.json" with { type: "json" };')
         .replace('import SNAPSHOT from "../snapshot.json";',
                  'import SNAPSHOT from "./snapshot.json" with { type: "json" };');
src = src.slice(0, src.lastIndexOf("export default")) +
      "export { payload, tiersNow, EXAMPLES, bazaarExtension };\n";
const dir = mkdtempSync(join(tmpdir(), "p0-examples-"));
writeFileSync(join(dir, "index.mjs"), src);
for (const f of ["catalog.slim.json", "snapshot.json"])
  writeFileSync(join(dir, f), readFileSync(join(root, f)));
const M = await import(pathToFileURL(join(dir, "index.mjs")).href);

const paidRoutes = Object.keys(M.tiersNow());

test("у каждого платного маршрута есть свой пример ответа", () => {
  const missing = paidRoutes.filter((p) => !M.EXAMPLES[p]);
  assert.deepEqual(missing, [],
    `маршруты без примера отдадут покупателю заглушку: ${missing.join(", ")}`);
});

test("пример не обещает полей, которых маршрут не отдаёт", async () => {
  for (const path of paidRoutes) {
    const real = await M.payload(path, new URL("https://x" + path));
    const example = M.EXAMPLES[path];
    for (const key of Object.keys(example)) {
      assert.ok(key in real,
        `${path}: пример обещает поле "${key}", которого в ответе нет`);
    }
    // Внутри массива — то же правило: элемент примера не богаче настоящего.
    for (const [key, val] of Object.entries(example)) {
      if (!Array.isArray(val) || !val.length || typeof val[0] !== "object") continue;
      const realArr = real[key];
      assert.ok(Array.isArray(realArr), `${path}: "${key}" в ответе не массив`);
      if (!realArr.length) continue;
      const realKeys = new Set(Object.keys(realArr[0]));
      for (const k of Object.keys(val[0])) {
        assert.ok(realKeys.has(k),
          `${path}: пример обещает ${key}[].${k}, которого в ответе нет`);
      }
    }
  }
});

test("пример — это ЗНАЧЕНИЯ, а не описания типов", () => {
  // Индексатор берёт queryParams как готовый вызов; описания типов вместо значений
  // уже однажды дали отказ «q is required». То же правило и для примера ответа:
  // заглушки вида "string" / "ISO-8601" покупателю ничего не говорят.
  const forbidden = ["ISO-8601", "see tier description", "string", "number", "<object>"];
  for (const path of paidRoutes) {
    const blob = JSON.stringify(M.EXAMPLES[path]);
    for (const bad of forbidden) {
      assert.ok(!blob.includes(`"${bad}"`),
        `${path}: в примере стоит заглушка "${bad}" вместо настоящего значения`);
    }
  }
});

test("пример попадает в расширение bazaar, а не лежит мёртвым грузом", () => {
  for (const path of paidRoutes) {
    const ext = M.bazaarExtension(path);
    assert.deepEqual(ext.bazaar.info.output.example, M.EXAMPLES[path],
      `${path}: расширение отдаёт не тот пример`);
  }
});
