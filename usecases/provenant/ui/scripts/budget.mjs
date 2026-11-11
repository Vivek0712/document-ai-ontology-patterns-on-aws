#!/usr/bin/env node
/**
 * Bundle budget (design targets, gzip): the initial route's JS ≤ 180 KB, each lazy chunk ≤ 90 KB, the
 * GraphCanvas chunk (dagre) ≤ 60 KB, CSS ≤ 25 KB. Reads `dist/` after `npm run build`; exits 1 over budget.
 *
 * Mock data has a budget too, because mock mode is how the demo runs: the `world.json` core is fetched at start
 * (≤ 256 KB gzip); each package shard (≤ 512 KB gzip) when a screen first touches that package. Run records,
 * artifact previews and X-ray pages are fetched on demand and are not counted.
 *
 *   npm run build && npm run budget
 */
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { gzipSync } from "node:zlib";

const KB = 1024;
const dist = new URL("../dist/", import.meta.url);
const html = readFileSync(new URL("index.html", dist), "utf8");
const gz = (file) => gzipSync(readFileSync(new URL(`assets/${file}`, dist))).length;
const entry = [...html.matchAll(/assets\/([\w.-]+\.js)/g)].map((m) => m[1]);
const css = [...html.matchAll(/assets\/([\w.-]+\.css)/g)].map((m) => m[1]);
const rows = [];
const check = (name, bytes, limit) => rows.push({ name, kb: bytes / KB, limit, ok: bytes <= limit * KB });

check(`initial JS (${entry.join(", ")})`, entry.reduce((s, f) => s + gz(f), 0), 180);
check("initial CSS", css.reduce((s, f) => s + gz(f), 0), 25);
for (const f of readdirSync(new URL("assets/", dist)).filter((f) => f.endsWith(".js") && !entry.includes(f))) {
  check(f, gz(f), f.startsWith("GraphCanvas-") ? 60 : 90);
}
const world = new URL("../mock-public/mock/world.json", import.meta.url);
if (existsSync(world)) {
  check("mock world.json core (fetched at start in mock mode)", gzipSync(readFileSync(world)).length, 256);
  // each package shard is fetched the first time a screen touches that package
  const shards = JSON.parse(readFileSync(world, "utf8")).shards ?? {};
  for (const [pkg, path] of Object.entries(shards)) {
    check(`mock shard ${pkg} (on demand)`, gzipSync(readFileSync(new URL(`../mock-public/mock/${path}`, import.meta.url))).length, 512);
  }
}
const over = rows.filter((r) => !r.ok);
for (const r of rows.sort((a, b) => b.kb - a.kb).slice(0, 9)) console.log(`${r.ok ? "ok  " : "OVER"} ${r.kb.toFixed(1).padStart(6)} KB / ${r.limit} KB  ${r.name}`);
console.log(over.length ? `${over.length} chunk(s) over budget` : `all ${rows.length} chunks within budget`);
process.exit(over.length ? 1 : 0);
