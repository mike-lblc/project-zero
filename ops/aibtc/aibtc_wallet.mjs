#!/usr/bin/env node
/**
 * КОШЕЛЁК-ПОДПИСАНТ АГЕНТА ДЛЯ ДОСКИ AIBTC (aibtc.com).
 *
 * Зачем свой, а не официальный @aibtc/mcp-server: тот тянет 2 МБ DeFi/nostr-
 * зависимостей, а нам нужны ровно четыре вещи — адреса, две подписи,
 * регистрация, сдача работы. Алгоритмы взяты из их исходников
 * (src/utils/bip322.ts, src/utils/bitcoin.ts, src/tools/signing.tools.ts):
 *   BTC   m/84'/0'/0'/0/0 → P2WPKH bc1q…; подпись — BIP-322 «simple»
 *   STX   @stacks/wallet-sdk generateWallet → accounts[0] → SP…;
 *         подпись — hashMessage + signMessageHashRsv (RSV, 0x-hex)
 *
 * Seed-фраза: переменная AIBTC_MNEMONIC (или строка AIBTC_MNEMONIC= в Brain/.env).
 * Команда create печатает фразу ОДИН раз и дописывает её в .env — сохраните её
 * сами (Xverse/Leather импортируют её как обычный BIP39). Ключи наружу не
 * печатаются никогда.
 *
 *   node aibtc_wallet.mjs create                 — новый кошелёк (24 слова), адреса
 *   node aibtc_wallet.mjs addresses              — адреса из сохранённой фразы
 *   node aibtc_wallet.mjs sign-btc "<текст>"     — BIP-322 подпись (base64)
 *   node aibtc_wallet.mjs sign-stx "<текст>"     — Stacks RSV подпись (0x-hex)
 *   node aibtc_wallet.mjs register ["описание"]  — POST /api/register
 *   node aibtc_wallet.mjs submit <bountyId> "<сообщение>" [contentUrl]
 *                                                — POST /api/bounties/{id}/submit
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { generateMnemonic, mnemonicToSeedSync, validateMnemonic } from "@scure/bip39";
import { wordlist } from "@scure/bip39/wordlists/english.js";
import { HDKey } from "@scure/bip32";
import * as btc from "@scure/btc-signer";
// @stacks/* — CommonJS: берём default-импорт и достаём имена из него.
import stacksEncryption from "@stacks/encryption";
import stacksCommon from "@stacks/common";
import stacksWalletSdk from "@stacks/wallet-sdk";
import stacksTransactions from "@stacks/transactions";
const { hashSha256Sync, hashMessage } = stacksEncryption;                       // хэши — в encryption
const { concatBytes } = stacksCommon;
const { generateWallet, getStxAddress } = stacksWalletSdk;
const { getAddressFromPublicKey, signMessageHashRsv, publicKeyFromSignatureRsv } = stacksTransactions;  // подписи — в transactions (v7)

const HERE = path.dirname(fileURLToPath(import.meta.url));
const BRAIN = path.resolve(HERE, "..", "..");
const ENV_FILE = path.join(BRAIN, ".env");
const API = "https://aibtc.com";
const REGISTER_MESSAGE = "Bitcoin will be the currency of AIs";

// ---------------------------------------------------------------- seed
function readEnvMnemonic() {
  if (process.env.AIBTC_MNEMONIC) return process.env.AIBTC_MNEMONIC.trim().toLowerCase();
  if (fs.existsSync(ENV_FILE)) {
    for (const line of fs.readFileSync(ENV_FILE, "utf8").split(/\r?\n/)) {
      if (line.startsWith("AIBTC_MNEMONIC=")) {
        return line.slice("AIBTC_MNEMONIC=".length).trim().replace(/^["']|["']$/g, "").toLowerCase();
      }
    }
  }
  return null;
}

function requireMnemonic() {
  const m = readEnvMnemonic();
  if (!m) fail("нет seed-фразы: сначала `create`, или положите AIBTC_MNEMONIC в Brain/.env");
  if (!validateMnemonic(m, wordlist)) fail("AIBTC_MNEMONIC не проходит проверку BIP39");
  return m;
}

function appendEnv(key, value) {
  const line = `${key}=${value}`;
  const existing = fs.existsSync(ENV_FILE) ? fs.readFileSync(ENV_FILE, "utf8") : "";
  if (existing.split(/\r?\n/).some((l) => l.startsWith(key + "="))) {
    fail(`${key} уже есть в .env — не перезаписываю существующий кошелёк`);
  }
  fs.appendFileSync(ENV_FILE, (existing.endsWith("\n") || existing === "" ? "" : "\n") + line + "\n", "utf8");
}

function fail(msg) {
  process.stderr.write(`ошибка: ${msg}\n`);
  process.exit(1);
}

// ---------------------------------------------------------------- BTC (как utils/bitcoin.ts)
function btcKey(mnemonic) {
  const seed = mnemonicToSeedSync(mnemonic);
  const key = HDKey.fromMasterSeed(seed).derive("m/84'/0'/0'/0/0");
  if (!key.privateKey || !key.publicKey) fail("не удалось вывести ключ BTC");
  const p2 = btc.p2wpkh(key.publicKey, btc.NETWORK);
  return { privateKey: key.privateKey, publicKey: key.publicKey, address: p2.address, script: p2.script };
}

// ---------------------------------------------------------------- BIP-322 «simple» (как utils/bip322.ts)
function bip322TaggedHash(message) {
  const tagHash = hashSha256Sync(new TextEncoder().encode("BIP0322-signed-message"));
  return hashSha256Sync(concatBytes(tagHash, tagHash, new TextEncoder().encode(message)));
}

function doubleSha256(data) {
  return hashSha256Sync(hashSha256Sync(data));
}

function bip322ToSpendTxid(message, scriptPubKey) {
  const scriptSig = concatBytes(new Uint8Array([0x00, 0x20]), bip322TaggedHash(message));
  const rawTx = btc.RawTx.encode({
    version: 0,
    inputs: [{ txid: new Uint8Array(32), index: 0xffffffff, finalScriptSig: scriptSig, sequence: 0 }],
    outputs: [{ amount: 0n, script: scriptPubKey }],
    lockTime: 0,
  });
  return doubleSha256(rawTx).reverse();
}

function bip322Sign(message, privateKey, scriptPubKey) {
  const toSpendTxid = bip322ToSpendTxid(message, scriptPubKey);
  const tx = new btc.Transaction({ version: 0, lockTime: 0, allowUnknownOutputs: true });
  tx.addInput({ txid: toSpendTxid, index: 0, sequence: 0, witnessUtxo: { amount: 0n, script: scriptPubKey } });
  tx.addOutput({ script: btc.Script.encode(["RETURN"]), amount: 0n });
  tx.signIdx(privateKey, 0);
  tx.finalizeIdx(0);
  const input = tx.getInput(0);
  if (!input.finalScriptWitness) fail("BIP-322: подпись не дала witness");
  return Buffer.from(btc.RawWitness.encode(input.finalScriptWitness)).toString("base64");
}

// ---------------------------------------------------------------- STX (как wallet-manager.ts / signing.tools.ts)
async function stxAccount(mnemonic) {
  const wallet = await generateWallet({ secretKey: mnemonic, password: "" });
  const account = wallet.accounts[0];
  return { privateKey: account.stxPrivateKey, address: getStxAddress(account, "mainnet") };
}

function stxSign(message, privateKey) {
  const msgHash = hashMessage(message);
  let sig = signMessageHashRsv({ messageHash: msgHash, privateKey });
  if (typeof sig !== "string") sig = sig.data ?? String(sig);
  // ГОЛЫЙ HEX БЕЗ 0x. Документация AIBTC пишет «с префиксом 0x», но сервер сам
  // приписывает 0x и на «0x0x…» отвечает INVALID_STX_SIGNATURE («Cannot convert
  // 0x0x… to a BigInt») — поймано на живой регистрации 14.09.2026. Их же
  // stacks_sign_message отдаёт 130 hex-символов без префикса.
  sig = sig.startsWith("0x") ? sig.slice(2) : sig;
  // самопроверка: адрес, восстановленный из подписи, обязан совпасть
  const pub = publicKeyFromSignatureRsv(msgHash, sig);
  const recovered = getAddressFromPublicKey(pub, "mainnet");
  return { signature: sig, recovered };
}

// ---------------------------------------------------------------- HTTP
async function post(pathname, body) {
  const r = await fetch(API + pathname, {
    method: "POST",
    headers: { "Content-Type": "application/json", "User-Agent": "P0-agents/1.0 (+https://github.com/mike-lblc/project-zero)" },
    body: JSON.stringify(body),
  });
  const text = await r.text();
  let data;
  try { data = JSON.parse(text); } catch { data = { raw: text.slice(0, 600) }; }
  return { status: r.status, data };
}

// ---------------------------------------------------------------- commands
async function cmdCreate() {
  if (readEnvMnemonic()) fail("seed уже есть (AIBTC_MNEMONIC) — используйте `addresses`");
  const mnemonic = generateMnemonic(wordlist, 256);
  appendEnv("AIBTC_MNEMONIC", mnemonic);
  const b = btcKey(mnemonic);
  const s = await stxAccount(mnemonic);
  process.stdout.write([
    "СОХРАНИТЕ ЭТУ SEED-ФРАЗУ (24 слова) — она показывается один раз и записана в Brain/.env:",
    "",
    mnemonic,
    "",
    `BTC (bc1q, m/84'/0'/0'/0/0): ${b.address}`,
    `STX (mainnet):               ${s.address}`,
    "",
    "Импорт в Xverse/Leather — как обычная фраза BIP39; путь BTC — Native SegWit.",
  ].join("\n") + "\n");
}

async function cmdAddresses() {
  const m = requireMnemonic();
  const b = btcKey(m);
  const s = await stxAccount(m);
  process.stdout.write(JSON.stringify({ btcAddress: b.address, stxAddress: s.address }, null, 2) + "\n");
}

async function cmdSignBtc(message) {
  if (!message) fail("нужен текст сообщения");
  const b = btcKey(requireMnemonic());
  process.stdout.write(JSON.stringify({ btcAddress: b.address, message, signature: bip322Sign(message, b.privateKey, b.script), format: "BIP-322 simple (base64)" }, null, 2) + "\n");
}

async function cmdSignStx(message) {
  if (!message) fail("нужен текст сообщения");
  const s = await stxAccount(requireMnemonic());
  const { signature, recovered } = stxSign(message, s.privateKey);
  if (recovered !== s.address) fail(`самопроверка STX не сошлась: ${recovered} != ${s.address}`);
  process.stdout.write(JSON.stringify({ stxAddress: s.address, message, signature, format: "RSV hex, без 0x (так ждёт сервер AIBTC)" }, null, 2) + "\n");
}

async function cmdRegister(description) {
  const m = requireMnemonic();
  const b = btcKey(m);
  const s = await stxAccount(m);
  const stx = stxSign(REGISTER_MESSAGE, s.privateKey);
  if (stx.recovered !== s.address) fail("самопроверка STX не сошлась — регистрацию не отправляю");
  const body = {
    bitcoinSignature: bip322Sign(REGISTER_MESSAGE, b.privateKey, b.script),
    stacksSignature: stx.signature,
    btcAddress: b.address,
    stxAddress: s.address,
    description: (description || "P0 — an 18-role autonomous agent collective. Clarity/Stacks audits, documentation and QA work for agent tooling, ranked x402 market data. Paid in sBTC/USDC/BTC.").slice(0, 280),
  };
  const r = await post("/api/register", body);
  const out = { http: r.status, btcAddress: b.address, stxAddress: s.address, response: r.data };
  // sponsorApiKey выдаётся один раз — сохраняем в .env, в вывод не печатаем
  const key = r.data && (r.data.sponsorApiKey || (r.data.sponsorKeyInfo && r.data.sponsorKeyInfo.apiKey));
  if (key) {
    try { appendEnv("AIBTC_SPONSOR_API_KEY", key); out.sponsorApiKey = "сохранён в Brain/.env"; } catch (e) { out.sponsorApiKey = `не сохранён: ${e.message}`; }
    if (r.data.sponsorApiKey) r.data.sponsorApiKey = "***";
    if (r.data.sponsorKeyInfo && r.data.sponsorKeyInfo.apiKey) r.data.sponsorKeyInfo.apiKey = "***";
  }
  process.stdout.write(JSON.stringify(out, null, 2) + "\n");
  process.exitCode = r.status >= 200 && r.status < 300 ? 0 : 2;
}

async function cmdSubmit(bountyId, message, contentUrl) {
  if (!bountyId || !message) fail("нужны bountyId и сообщение");
  const b = btcKey(requireMnemonic());
  const signedAt = new Date().toISOString();
  const url = contentUrl || "";
  // Формат из /api/bounties/{id}/submit: "AIBTC Bounty Submit | {bountyId} | {submitterBtcAddress} | {message} | {contentUrl} | {signedAt}"
  const toSign = `AIBTC Bounty Submit | ${bountyId} | ${b.address} | ${message} | ${url} | ${signedAt}`;
  const body = { submitterBtcAddress: b.address, message, contentUrl: url, signedAt, signature: bip322Sign(toSign, b.privateKey, b.script) };
  const r = await post(`/api/bounties/${encodeURIComponent(bountyId)}/submit`, body);
  process.stdout.write(JSON.stringify({ http: r.status, bountyId, btcAddress: b.address, response: r.data }, null, 2) + "\n");
  process.exitCode = r.status >= 200 && r.status < 300 ? 0 : 2;
}

const [cmd, ...args] = process.argv.slice(2);
const table = {
  create: () => cmdCreate(),
  addresses: () => cmdAddresses(),
  "sign-btc": () => cmdSignBtc(args[0]),
  "sign-stx": () => cmdSignStx(args[0]),
  register: () => cmdRegister(args[0]),
  submit: () => cmdSubmit(args[0], args[1], args[2]),
};
if (!table[cmd]) {
  process.stderr.write("команды: create | addresses | sign-btc <msg> | sign-stx <msg> | register [описание] | submit <bountyId> <msg> [contentUrl]\n");
  process.exit(1);
}
table[cmd]().catch((e) => fail(e && e.stack ? e.stack.split("\n")[0] : String(e)));
