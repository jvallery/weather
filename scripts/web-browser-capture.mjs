// SPDX-License-Identifier: MIT
/** Optional ROOT qualification custody window; never used by event/required CI. */
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { lstatSync, mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { setTimeout } from "node:timers/promises";
const operation = process.env.WEATHER_BROWSER_CAPTURE_OPERATION;
if (!operation) process.exit(0);
if (process.platform !== "linux" || process.env.WEATHER_CI_IN_CLUSTER !== "1") throw new Error("Capture requires approved in-cluster qualification");
const source = process.env.WEATHER_BROWSER_CAPTURE_SOURCE;
const seconds = Number(process.env.WEATHER_BROWSER_CAPTURE_SECONDS || "180");
if (!/^[a-z0-9][a-z0-9-]{2,62}$/.test(operation) || !/^[a-f0-9]{40}$/.test(source || "") || !Number.isInteger(seconds) || seconds < 30 || seconds > 300) throw new Error("Invalid bounded capture identity/deadline");
if (execFileSync("git", ["rev-parse", "HEAD"], { encoding: "utf8" }).trim() !== source) throw new Error("Capture source differs from actual checkout");
const reportPath = "/workspace/ci-test-report.json";
const directory = "/workspace/weather-browser-capture";
function hashed(path, maximum) {
  const stat = lstatSync(path);
  if (!stat.isFile() || stat.size > maximum) throw new Error("Invalid capture evidence size/type");
  const bytes = readFileSync(path);
  return { path, bytes: bytes.length, sha256: createHash("sha256").update(bytes).digest("hex") };
}
const reportFile = hashed(reportPath, 60_000);
const report = JSON.parse(readFileSync(reportPath, "utf8"));
let total = 0;
const screenshots = report.screenshots.map((item) => {
  if (!/^artifacts\/synthetic-[a-z]+-(320|768|1440)-(chromium|webkit|firefox)\.png$/.test(item.path)) throw new Error("Invalid capture path");
  const file = hashed(item.path, 4 * 1024 * 1024);
  total += file.bytes;
  if (file.sha256 !== item.sha256 || file.bytes !== item.bytes || total > 32 * 1024 * 1024) throw new Error("Capture screenshot integrity/budget failure");
  return file;
});
const deadline = Date.now() + seconds * 1000;
const ready = { schema: "weather.web-browser-capture/v1", operation_id: operation, source_revision: source, pod_name: process.env.HOSTNAME || null, deadline: new Date(deadline).toISOString(), report: reportFile, screenshots, synthetic_only: true };
const manifest = `${JSON.stringify(ready)}\n`;
const manifestSha = createHash("sha256").update(manifest).digest("hex");
mkdirSync(directory, { recursive: true });
writeFileSync(`${directory}/ready.tmp`, manifest);
renameSync(`${directory}/ready.tmp`, `${directory}/ready.json`);
process.stdout.write(`CAPTURE_READY ${operation}; ${screenshots.length} synthetic screenshots; deadline ${ready.deadline}\n`);
let captured = false;
while (Date.now() < deadline) {
  try {
    const ackPath = `${directory}/captured.json`;
    hashed(ackPath, 1024);
    const ack = JSON.parse(readFileSync(ackPath, "utf8"));
    if (ack.operation_id !== operation || ack.source_revision !== source || ack.manifest_sha256 !== manifestSha) throw new Error("Capture acknowledgement does not bind this run");
    captured = true;
    break;
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
  await setTimeout(500);
}
report.operator_capture = { operation_id: operation, manifest_sha256: manifestSha, status: captured ? "acknowledged" : "timed_out", custody: "ROOT copied and verified the ready manifest before acknowledging; external readback is operator evidence" };
if (!captured) report.result = "failed";
const final = `${JSON.stringify(report)}\n`;
if (Buffer.byteLength(final) > 60_000) throw new Error("Captured report exceeds Argo parameter budget");
writeFileSync(reportPath, final);
if (!captured) { process.stderr.write("Browser evidence capture timed out\n"); process.exitCode = 1; }
