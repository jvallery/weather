// SPDX-License-Identifier: MIT
import { randomBytes } from "node:crypto";
import { performance } from "node:perf_hooks";
import { clearTimeout, setTimeout } from "node:timers";
import express from "express";
import { createRequestHandler } from "@react-router/express";
import { collectDefaultMetrics, register, Histogram, Gauge, Counter } from "prom-client";
import { securityHeaders } from "./security-headers.mjs";

const app = express();
app.disable("x-powered-by");
app.set("trust proxy", 1);
const timings = new Histogram({ name: "weather_web_request_duration_seconds", help: "Web route duration", labelNames: ["route", "status"] });
function routeName(path) {
  if (path.startsWith("/assets/") || path.startsWith("/fonts/")) return "assets";
  if (/^\/day\/\d{4}-\d\d-\d\d\/?$/.test(path)) return "/day/:date/";
  if (/^\/timelapses\/[^/]+\/[^/]+\/[^/]+\/?$/.test(path)) return "/timelapses/:camera/:date/:segment/";
  if (/^\/archive\/\d{4}\/\d\d\/?$/.test(path)) return "/archive/:year/:month/";
  return ["/", "/about/", "/archive/", "/weather/history/", "/weather/almanac/", "/events/", "/search/", "/embed/weather/", "/editor/", "/healthz", "/readyz"].includes(path) ? path : "/other";
}
app.use((req, res, next) => {
  const nonce = randomBytes(24).toString("base64");
  req.headers["x-weather-csp-nonce"] = nonce;
  res.set(securityHeaders(req.path, nonce));
  const started = performance.now();
  res.on("finish", () => {
    const duration = (performance.now() - started) / 1000;
    const route = routeName(req.path);
    timings.labels(route, String(res.statusCode)).observe(duration);
    // Successful kubelet probes are not logged (#147: about 540 lines an hour per pod); a failing probe still is.
    const probe = (route === "/healthz" || route === "/readyz") && res.statusCode < 400;
    if (route !== "assets" && !probe) process.stdout.write(`${JSON.stringify({ service: "weather-web", route, status: res.statusCode, duration_ms: Math.round(duration * 1000) })}\n`);
  });
  if (req.originalUrl.length > 4096) return res.sendStatus(414);
  next();
});
app.get(["/healthz", "/readyz"], (_req, res) => res.json({ status: "ok", service: "weather-web" }));
app.use((req, res, next) => {
  const expected = process.env.PUBLIC_HOST || "weather.vallery.net";
  const host = (req.headers.host || "").split(":")[0];
  const fixture = process.env.WEATHER_CI_IN_CLUSTER === "1" && host === "localhost";
  if (host !== expected && !fixture) return res.sendStatus(400);
  req.headers["x-forwarded-host"] = fixture ? req.headers.host : expected;
  req.headers["x-forwarded-proto"] = fixture ? "http" : "https";
  if (req.path === "/metrics" || req.path.endsWith(".map")) return res.sendStatus(404);
  next();
});
app.use(express.static("build/client", { immutable: true, maxAge: "1y", setHeaders(res) { res.setHeader("Cache-Control", "public, max-age=31536000, immutable"); } }));
let rendering = 0;
const renderFailures = new Counter({ name: "weather_web_ssr_failures_total", help: "Bounded SSR failures by cause", labelNames: ["reason"] });
for (const reason of ["overloaded", "deadline", "render_error"]) renderFailures.labels(reason).inc(0);
const activeRenders = new Gauge({ name: "weather_web_ssr_inflight", help: "Active bounded document/data responses" });
// Only routes whose loaders read the API take a slot. Loader-less pages (the editor fetches its
// private data in the browser, #44), their data requests and
// the lazy route manifest never wait behind API work.
const UNMETERED = /^\/(?:(?:about|search|editor)(?:\/|\/_\.data|\.data)?|__manifest)$/;
app.use((req, res, next) => {
  if (UNMETERED.test(req.path)) return next();
  // A hydrated client turns a plain 503 on its single-fetch .data request into a route error response
  // whose data is this body; "busy" is the marker the root ErrorBoundary renders as the busy state.
  if (rendering >= 4) { renderFailures.labels("overloaded").inc(); res.setHeader("Retry-After", "5"); return res.status(503).type("text").send(req.path.endsWith(".data") ? "busy" : "Weather is busy. Please retry shortly."); }
  rendering += 1; activeRenders.inc();
  let released = false;
  const timer = setTimeout(() => { renderFailures.labels("deadline").inc(); res.destroy(); }, 15_000); timer.unref();
  const release = () => { if (!released) { released = true; rendering -= 1; activeRenders.dec(); clearTimeout(timer); } };
  res.once("finish", release); res.once("close", release);
  next();
});
app.use(createRequestHandler({ build: () => import("./build/server/index.js") }));
app.use((_error, _req, res, _next) => { renderFailures.labels("render_error").inc(); if (res.headersSent) { res.destroy(); return; } res.status(503).type("text").send("Weather is temporarily unavailable."); });
const server = app.listen(Number(process.env.PORT || 3000), "0.0.0.0");
server.requestTimeout = 15_000;
server.headersTimeout = 10_000;
server.keepAliveTimeout = 5000;
server.maxHeadersCount = 64;
collectDefaultMetrics();
const metrics = express();
metrics.disable("x-powered-by");
metrics.get("/metrics", async (_req, res) => res.type(register.contentType).send(await register.metrics()));
const metricsServer = metrics.listen(Number(process.env.METRICS_PORT || 9000), "0.0.0.0");
process.on("SIGTERM", () => {
  server.close(); metricsServer.close();
  setTimeout(() => process.exit(0), 10_000).unref();
});
