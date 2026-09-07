/**
 * verify_onboarding.mjs - PW App Kit v2 self-check.
 *
 * Run from the app folder after copying pw_access.js:
 *
 *   node verify_onboarding.mjs [path/to/.env] [optional_google_token]
 */

import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { APP_NAME, PROXY_BASE_URL, checkAllowedStatus } from "./pw_access.js";

const envPath = process.argv[2] || ".env";
const token = process.argv[3] || "";
let ok = true;

const providerKeyHints = /GEMINI|MATHPIX|SARVAM|ELEVEN|ANTHROPIC|CLAUDE|LITELLM|OPENAI/i;
const oldPatternHints = ["log:false", '"log": false', "'log': false", "/api/usage-log", "UsageSession.flush", ".flush()"];
const skipDirs = new Set([".git", "node_modules", "__pycache__", "dist", "build", ".venv", "venv"]);

async function fetchWithTimeout(url, ms) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), ms);
  try {
    return await fetch(url, { signal: ctrl.signal, headers: { connection: "close" } });
  } finally {
    clearTimeout(timer);
  }
}

function scanEnvForKeys(path) {
  const leaked = [];
  if (!existsSync(path)) return leaked;
  for (const line of readFileSync(path, "utf8").split(/\r?\n/)) {
    const s = line.trim();
    if (!s || s.startsWith("#") || !s.includes("=")) continue;
    const eq = s.indexOf("=");
    const name = s.slice(0, eq).trim();
    const value = s.slice(eq + 1).trim();
    if (value && providerKeyHints.test(name)) leaked.push(name);
  }
  return leaked;
}

function scanOldPatterns(root = ".") {
  const hits = [];
  function walk(dir) {
    for (const entry of readdirSync(dir)) {
      if (skipDirs.has(entry)) continue;
      const path = join(dir, entry);
      const st = statSync(path);
      if (st.isDirectory()) {
        walk(path);
        continue;
      }
      if (!/\.(py|js|jsx|ts|tsx|md)$/i.test(entry)) continue;
      const normalized = path.replaceAll("\\", "/");
      if (normalized.includes("pw-app-kit/")) continue;
      const text = readFileSync(path, "utf8");
      const found = oldPatternHints.find((hint) => text.includes(hint));
      if (found) hits.push(`${path}: ${found}`);
      if (hits.length >= 20) return;
    }
  }
  try {
    walk(root);
  } catch {
    return hits;
  }
  return hits;
}

console.log(`APP_NAME = ${JSON.stringify(APP_NAME)}`);
console.log(`PROXY    = ${PROXY_BASE_URL}`);

if (APP_NAME === "SET-YOUR-APP-NAME") {
  console.log("FAIL: APP_NAME is still the placeholder.");
  ok = false;
}

try {
  const r = await fetchWithTimeout(`${PROXY_BASE_URL}/api/apps`, 20000);
  const apps = r.ok ? (await r.json()).apps || [] : [];
  if (apps.includes(APP_NAME)) {
    console.log(`PASS: ${JSON.stringify(APP_NAME)} is registered on the proxy`);
  } else {
    console.log(`FAIL: ${JSON.stringify(APP_NAME)} not in /api/apps. Add its exact column to Whitelisted.`);
    ok = false;
  }
} catch (e) {
  console.log("WARN: could not reach /api/apps:", e.message);
  ok = false;
}

const leaked = scanEnvForKeys(envPath);
if (leaked.length) {
  console.log(`FAIL: provider keys still present in ${envPath}: ${leaked.join(", ")}`);
  ok = false;
} else {
  console.log(`PASS: no provider keys in ${envPath}`);
}

const oldHits = scanOldPatterns(".");
if (oldHits.length) {
  console.log("WARN: possible old combined-logging patterns found:");
  for (const hit of oldHits) console.log(`  ${hit}`);
  console.log("      Remove these unless they are intentional legacy/manual audit code.");
} else {
  console.log("PASS: no obvious old combined-logging patterns found");
}

if (token) {
  const status = await checkAllowedStatus(token);
  console.log(`allowlist check for supplied token: ${status}`);
  if (status === "error") console.log("  token invalid/expired, proxy unreachable, or server error");
  ok = ok && (status === "allowed" || status === "denied");
}

console.log("\nRESULT:", ok ? "ALL GOOD" : "ISSUES FOUND");
process.exitCode = ok ? 0 : 1;
