// SPDX-License-Identifier: MIT
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { retainWebAssets } from "./retain-web-assets.mjs";
function trees(t) {
  const root = mkdtempSync(join(tmpdir(), "weather-assets-")); t.after(() => rmSync(root, { recursive: true, force: true }));
  const dirs = ["old", "current", "next"].map((name) => join(root, name));
  for (const dir of dirs) mkdirSync(join(dir, "assets"), { recursive: true });
  return dirs;
}
test("one predecessor survives while its older ancestor drops out", (t) => {
  const [old, current, next] = trees(t);
  writeFileSync(join(old, "assets/old-aaa.js"), "older source"); writeFileSync(join(current, "assets/current-bbb.js"), "current source");
  retainWebAssets(current, old, true);
  assert.equal(readFileSync(join(current, "assets/old-aaa.js"), "utf8"), "older source");
  writeFileSync(join(next, "assets/next-ccc.js"), "next source"); retainWebAssets(next, current);
  assert.equal(readFileSync(join(next, "assets/current-bbb.js"), "utf8"), "current source");
  assert.throws(() => readFileSync(join(next, "assets/old-aaa.js")), { code: "ENOENT" });
});
test("different bytes under one filename fail without replacing current data", (t) => {
  const [old, current] = trees(t); writeFileSync(join(old, "assets/same.js"), "old"); writeFileSync(join(current, "assets/same.js"), "new");
  assert.throws(() => retainWebAssets(current, old, true), /collision/);
  assert.equal(readFileSync(join(current, "assets/same.js"), "utf8"), "new");
});
test("inventory checksum drift and path escape fail closed", (t) => {
  const [old, current, next] = trees(t); writeFileSync(join(old, "assets/a.js"), "old"); writeFileSync(join(current, "assets/b.js"), "current");
  retainWebAssets(current, old, true); writeFileSync(join(current, "assets/b.js"), "tampered");
  assert.throws(() => retainWebAssets(next, current), /differ/);
  writeFileSync(join(current, ".weather-assets.json"), JSON.stringify({ schema: 1, assets: [{ path: "assets/../../escape.js", bytes: 1, sha256: "a".repeat(64) }] }));
  assert.throws(() => retainWebAssets(next, current), /Invalid asset path/);
});
test("missing inventory requires the explicit first-baseline exception", (t) => {
  const [old, current] = trees(t); assert.throws(() => retainWebAssets(current, old), /lacks/);
});
