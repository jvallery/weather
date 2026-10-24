// SPDX-License-Identifier: MIT
/* global window */
/** ROOT qualification: hold real old tabs across one explicit promotion, with no GitHub publisher. */
import { chromium, firefox, webkit } from "@playwright/test";
import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { setTimeout as sleep } from "node:timers/promises";
import { parseArgs } from "node:util";
const { values } = parseArgs({ options: {
  "operation-id": { type: "string" }, "source-revision": { type: "string" },
  "old-image-digest": { type: "string" }, "expected-image-digest": { type: "string" },
  "deadline-seconds": { type: "string", default: "1200" },
  "output-dir": { type: "string", default: "/workspace/weather-web-rollout" },
  report: { type: "string", default: "/workspace/ci-test-report.json" },
  url: { type: "string", default: "https://weather.vallery.net/" },
} });
const operationId = values["operation-id"];
const sourceRevision = values["source-revision"];
const oldDigest = values["old-image-digest"];
const expectedDigest = values["expected-image-digest"];
const directory = values["output-dir"];
const started = new Date().toISOString();
const deadlineSeconds = Number(values["deadline-seconds"]);
if (process.platform !== "linux" || process.env.WEATHER_CI_IN_CLUSTER !== "1") throw new Error("Rollout requires approved in-cluster CI");
if (!/^[a-z0-9-]{3,63}$/.test(operationId || "") || !/^[0-9a-f]{40}$/.test(sourceRevision || "")) throw new Error("Invalid operation/source identity");
if (![oldDigest, expectedDigest].every((value) => /^sha256:[0-9a-f]{64}$/.test(value || "")) || oldDigest === expectedDigest) throw new Error("Two distinct pinned web image digests are required");
if (!Number.isInteger(deadlineSeconds) || deadlineSeconds < 60 || deadlineSeconds > 3600) throw new Error("Invalid bounded deadline");
const target = new URL(values.url);
if (target.origin !== "https://weather.vallery.net" || target.username || target.password || target.search || target.hash) throw new Error("Rollout target must be the public Weather origin");
const deadline = Date.now() + deadlineSeconds * 1000;
const sessions = [];
const browsers = [];
const captured = new Map();
function remaining() { const value = deadline - Date.now(); if (value <= 0) throw new Error("Rollout qualification deadline expired"); return Math.min(30_000, value); }
function assetUrl(value) {
  const url = new URL(value, target);
  if (url.origin !== target.origin || !/^\/assets\/[A-Za-z0-9_./-]+$/.test(url.pathname) || url.href.length > 512) throw new Error("Unexpected non-public asset identity");
  url.search = ""; url.hash = ""; return url;
}
async function assetProof(request, asset, phase) {
  const url = new URL(asset); url.searchParams.set("weather_rollout", `${operationId}-${phase}`);
  const response = await request.get(url.href, { timeout: remaining(), maxRedirects: 0 });
  if (response.status() !== 200) throw new Error(`Old asset is unavailable after ${phase}`);
  if (Number(response.headers()["content-length"] || 0) > 8 * 1024 * 1024) throw new Error("Asset exceeds qualification byte budget");
  const bytes = await response.body();
  if (bytes.length > 8 * 1024 * 1024) throw new Error("Asset exceeds qualification byte budget");
  return { url: asset, path: new URL(asset).pathname, bytes: bytes.length, sha256: createHash("sha256").update(bytes).digest("hex") };
}
async function emit(path, data) {
  const json = JSON.stringify(data);
  if (Buffer.byteLength(json) > 60_000) throw new Error("Qualification report exceeds bounded Argo parameter budget");
  await writeFile(path, `${json}\n`);
}
async function promotion() {
  const file = join(directory, "promotion-intent.json");
  let intended;
  while (Date.now() < deadline) {
    try {
      const raw = await readFile(file, "utf8");
      if (Buffer.byteLength(raw) > 60_000) throw new Error("Promotion receipt exceeds byte budget");
      const value = JSON.parse(raw);
      if (value.operation_id !== operationId || value.expected_image_digest !== expectedDigest || !/^[0-9a-f]{40}$/.test(value.intended_revision || "")) throw new Error("Promotion intent does not match exact qualification identity");
      if (intended && intended !== value.intended_revision) throw new Error("Promotion target changed during qualification");
      intended = value.intended_revision;
      if (value.phase === "promoted") {
        if (value.synced_revision !== intended || value.argo_sync !== "Synced" || value.argo_health !== "Healthy" || value.running_image_digest !== expectedDigest) throw new Error("Promotion readback does not prove the intended deployment");
        if (!Array.isArray(value.web_pods) || value.web_pods.length < 2 || value.web_pods.some((pod) => !pod.uid || pod.ready !== true || pod.image_digest !== expectedDigest)) throw new Error("Both ready web Pods must run the intended digest");
        if (!Array.isArray(value.origin_assets)) throw new Error("Origin asset readback is required to rule out a CDN-only success");
        for (const asset of captured.values()) if (!value.origin_assets.some((row) => row.path === asset.path && row.sha256 === asset.sha256 && row.bytes === asset.bytes)) throw new Error("Old asset missing from running-origin readback");
        return { intended_revision: intended, argo_sync: "Synced", argo_health: "Healthy", running_image_digest: expectedDigest,
          web_pods: value.web_pods.map((pod) => ({ uid: pod.uid, ready: true, image_digest: pod.image_digest })), origin_readback_assets: captured.size };
      }
      if (value.phase !== "intent") throw new Error("Unknown promotion phase");
    } catch (error) { if (error.code !== "ENOENT") throw error; }
    await sleep(500);
  }
  throw new Error("Timed out waiting for exact ROOT promotion readback");
}
async function main() {
  await mkdir(directory, { recursive: true });
  for (const [name, engine] of [["chromium", chromium], ["webkit", webkit], ["firefox", firefox]]) {
    const browser = await engine.launch({ headless: true, timeout: remaining() });
    browsers.push(browser);
    const context = await browser.newContext();
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", (error) => errors.push(String(error.message).slice(0, 200)));
    const response = await page.goto(target.href, { waitUntil: "networkidle", timeout: remaining() });
    if (response?.status() !== 200 || new URL(page.url()).origin !== target.origin) throw new Error("Old live page is not publicly readable");
    const heading = (await page.locator("h1").first().innerText()).trim();
    if (!heading || errors.length) throw new Error("Old tab did not become readable without browser errors");
    const manifest = await page.evaluate(() => {
      const value = window.__reactRouterManifest;
      if (!value?.routes) return null;
      return { modules: Object.values(value.routes).map((route) => route.module).filter(Boolean),
        assets: Object.values(value.routes).flatMap((route) => [route.module, ...(route.imports || []), ...(route.css || [])]).filter(Boolean) };
    });
    if (!manifest?.modules.length) throw new Error("Old tab has no hydrated route manifest; no rolling proof can be claimed");
    const domAssets = await page.locator("script[src],link[rel=stylesheet]").evaluateAll((elements) => elements.map((element) => element.src || element.href));
    for (const value of [...manifest.assets, ...domAssets]) {
      const url = assetUrl(value).href;
      if (captured.size >= 64 && !captured.has(url)) throw new Error("Old asset count exceeds bounded qualification budget");
      if (!captured.has(url)) captured.set(url, await assetProof(context.request, url, "before"));
    }
    const module = manifest.modules.find((value) => /\.m?js(?:$|\?)/.test(value));
    if (!module) throw new Error("Old tab has no route module to import after promotion");
    sessions.push({ name, browser, context, page, heading, module: assetUrl(module).href, errors, version: browser.version() });
  }
  const ready = { kind: "weather-web-rollout-ready-v1", operation_id: operationId, source_revision: sourceRevision,
    old_image_digest: oldDigest, expected_image_digest: expectedDigest, started_at: started,
    expires_at: new Date(deadline).toISOString(), assets: [...captured.values()], engines: sessions.map(({ name, version, heading, module }) => ({ name, version, heading, late_module: module })) };
  await emit(join(directory, "ready.json"), ready);
  process.stdout.write(`${JSON.stringify({ state: "READY", operation_id: operationId, ready_file: join(directory, "ready.json"), promotion_file: join(directory, "promotion-intent.json") })}\n`);
  const operator = await promotion();
  const outcomes = [];
  for (const session of sessions) {
    for (const asset of captured.values()) {
      const after = await assetProof(session.context.request, asset.url, `after-${session.name}`);
      if (after.sha256 !== asset.sha256 || after.bytes !== asset.bytes) throw new Error("Old hashed asset changed after promotion");
    }
    const importUrl = new URL(session.module); importUrl.searchParams.set("weather_rollout_import", `${operationId}-${session.name}`);
    const exports = await session.page.evaluate(async (url) => Object.keys(await import(url)).sort(), importUrl.href);
    if (!exports.length || session.errors.length || (await session.page.locator("h1").first().innerText()).trim() !== session.heading) throw new Error("Old tab failed module import or readability after promotion");
    const fresh = await session.context.newPage();
    const freshErrors = [];
    fresh.on("pageerror", (error) => freshErrors.push(String(error.message).slice(0, 200)));
    const response = await fresh.goto(target.href, { waitUntil: "networkidle", timeout: remaining() });
    if (response?.status() !== 200 || !(await fresh.locator("h1").first().innerText()).includes("Today")) throw new Error("Promoted Today page is not publicly readable");
    const policy = response.headers()["content-security-policy"] || "";
    if (policy.includes("unsafe-inline") || !policy.includes("frame-ancestors 'none'") || response.headers()["x-frame-options"] !== "DENY") throw new Error("Promoted page does not carry the required security policy");
    await fresh.evaluate((marker) => { window.__weatherRolloutMarker = marker; }, operationId);
    await fresh.getByRole("link", { name: "About", exact: true }).click({ timeout: remaining() });
    await fresh.waitForURL("**/about/", { timeout: remaining() });
    const navigationKeptDocument = await fresh.evaluate((marker) => window.__weatherRolloutMarker === marker, operationId);
    if (!navigationKeptDocument || freshErrors.length || !(await fresh.locator("h1").first().innerText()).includes("About")) throw new Error("Promoted client navigation did not hydrate correctly");
    await fresh.close();
    outcomes.push({ engine: session.name, version: session.version, old_heading: session.heading, late_module_imported: true, export_count: exports.length, assets_unchanged: captured.size, fresh_today_readable: true, fresh_client_navigation: true });
  }
  const report = { kind: "weather-web-rollout-result-v1", status: "passed", operation_id: operationId, source_revision: sourceRevision,
    old_image_digest: oldDigest, expected_image_digest: expectedDigest, started_at: started, finished_at: new Date().toISOString(),
    operator_promotion_readback: operator, assets: [...captured.values()], outcomes };
  await emit(values.report, report);
  process.stdout.write(`${JSON.stringify({ state: "PASSED", operation_id: operationId, report: values.report, assets: captured.size, engines: outcomes.length })}\n`);
}
try { await main(); }
catch (error) {
  const report = { kind: "weather-web-rollout-result-v1", status: "failed", operation_id: operationId, source_revision: sourceRevision,
    old_image_digest: oldDigest, expected_image_digest: expectedDigest, started_at: started, finished_at: new Date().toISOString(), reason: String(error.message).split("\n")[0].slice(0, 300) };
  await emit(values.report, report);
  process.stdout.write(`${JSON.stringify({ state: "FAILED", operation_id: operationId, reason: report.reason, report: values.report })}\n`);
  process.exitCode = 1;
} finally { await Promise.allSettled(browsers.map((browser) => browser.close())); }
