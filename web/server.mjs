// SPDX-License-Identifier: MIT
import { randomBytes } from "node:crypto";
import { performance } from "node:perf_hooks";
import { clearTimeout, setTimeout } from "node:timers";
import express from "express";
import { createRequestHandler } from "@react-router/express";
import { collectDefaultMetrics, register, Histogram, Gauge, Counter } from "prom-client";

const app = express();
app.disable("x-powered-by");
app.set("trust proxy", 1);
const timings = new Histogram({ name: "weather_web_request_duration_seconds", help: "Web route duration", labelNames: ["route", "status"] });
function routeName(path) {
  if (path.startsWith("/assets/") || path.startsWith("/fonts/")) return "assets";
  if (/^\/day\/\d{4}-\d\d-\d\d\/?$/.test(path)) return "/day/:date/";
  if (/^\/timelapses\/[^/]+\/[^/]+\/[^/]+\/?$/.test(path)) return "/timelapses/:camera/:date/:segment/";
  if (/^\/archive\/\d{4}\/\d\d\/?$/.test(path)) return "/archive/:year/:month/";
  return ["/", "/about/", "/archive/", "/weather/history/", "/search/", "/healthz", "/readyz"].includes(path) ? path : "/other";
}
app.use((req, res, next) => {
  const nonce = randomBytes(24).toString("base64");
  req.headers["x-weather-csp-nonce"] = nonce;
  res.set({ "Content-Security-Policy": `default-src 'self'; script-src 'self' 'nonce-${nonce}'; style-src 'self'; font-src 'self'; img-src 'self'; media-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'`, "X-Frame-Options": "DENY", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "Cache-Control": "no-store" });
  const started = performance.now();
  res.on("finish", () => {
    const duration = (performance.now() - started) / 1000;
    const route = routeName(req.path);
    timings.labels(route, String(res.statusCode)).observe(duration);
    if (route !== "assets") process.stdout.write(`${JSON.stringify({ service: "weather-web", route, status: res.statusCode, duration_ms: Math.round(duration * 1000) })}\n`);
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
const activeRenders = new Gauge({ name: "weather_web_ssr_inflight", help: "Active bounded document/data responses" });
app.use((_req, res, next) => {
  if (rendering >= 4) { renderFailures.labels("overloaded").inc(); res.setHeader("Retry-After", "5"); return res.status(503).type("text").send("Weather is busy. Please retry shortly."); }
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
