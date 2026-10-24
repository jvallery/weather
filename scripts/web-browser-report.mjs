// SPDX-License-Identifier: MIT
/** Retain compact fixture outcomes in the existing repo-validate report parameter. */
import { createHash } from "node:crypto";
import { lstatSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
if (process.platform !== "linux" || process.env.WEATHER_CI_IN_CLUSTER !== "1") throw new Error("Browser evidence requires approved fleet CI");
const input = "artifacts/playwright-results.json";
if (!lstatSync(input).isFile() || lstatSync(input).size > 16 * 1024 * 1024) throw new Error("Invalid browser report size/type");
const value = JSON.parse(readFileSync(input, "utf8"));
const projects = value.config.projects.map((project) => project.name).sort();
const failures = [];
function walk(suites) {
  for (const suite of suites) {
    for (const spec of suite.specs || []) for (const test of spec.tests || []) if (test.status !== "expected") failures.push({ title: spec.title, engine: test.projectName, status: test.status });
    walk(suite.suites || []);
  }
}
walk(value.suites || []);
const screenshots = [];
let total = 0;
for (const file of readdirSync("artifacts").filter((file) => /^synthetic-[a-z]+-(320|768|1440)-(chromium|webkit|firefox)\.png$/.test(file)).sort()) {
  const path = `artifacts/${file}`, stat = lstatSync(path);
  if (!stat.isFile() || stat.size > 4 * 1024 * 1024 || (total += stat.size) > 32 * 1024 * 1024) throw new Error("Screenshot evidence exceeds size/type budget");
  screenshots.push({ path, bytes: stat.size, sha256: createHash("sha256").update(readFileSync(path)).digest("hex") });
}
const exitCode = Number(process.argv[2]);
const complete = projects.join(",") === "chromium,firefox,webkit" && value.stats.expected >= 93 && value.stats.unexpected === 0 && value.stats.flaky === 0 && value.stats.skipped === 0 && failures.length === 0 && screenshots.length === 27;
const report = { schema: "weather.web-browser-fixtures/v1", result: exitCode === 0 && complete ? "passed" : "failed", engine_projects: projects, stats: value.stats, failures, screenshots, synthetic_only: true };
const json = JSON.stringify(report);
if (Buffer.byteLength(json) > 60_000) throw new Error("Browser evidence exceeds Argo parameter budget");
writeFileSync("/workspace/ci-test-report.json", `${json}\n`);
process.stdout.write(`Browser evidence: ${report.result}; ${screenshots.length} checksummed screenshots; report /workspace/ci-test-report.json\n`);
if (report.result !== "passed") process.exitCode = 1;
