// SPDX-License-Identifier: MIT
/** CI-only local equivalent of deploy/k8s's IngressRoutes (#21): the same routes, priorities and
 * Middlewares, from fixture-ingress.json (rendered from deploy/k8s by scripts/fixture_ingress.py;
 * a unit test keeps them equal). Security headers, per-visitor rate limits (token buckets keyed on
 * Cf-Connecting-Ip) and in-flight limits behave as Traefik's; a refused request is a 429.
 * Fixture deviations, by design: a request without Cf-Connecting-Ip is not limited (Cloudflare
 * always sets it in production; the browser suite sends none), and the cross-namespace editor
 * auth Middlewares listed under `excluded` are not run. */
import { readFileSync } from "node:fs";
import { createServer, request } from "node:http";
if (process.platform !== "linux" || process.env.WEATHER_CI_IN_CLUSTER !== "1") throw new Error("Fixture ingress requires approved in-cluster CI");
const table = JSON.parse(readFileSync(new URL("./fixture-ingress.json", import.meta.url)));
const PORTS = { api: 4174, web: 4175 };

/** Path predicates of a Traefik rule; a rule with none (Host only) matches every path. */
function predicates(match) {
  const found = [...match.matchAll(/(PathPrefix|PathRegexp|Path)\(`([^`]*)`\)/g)].map(([, kind, value]) =>
    kind === "PathPrefix" ? (path) => path.startsWith(value) : kind === "Path" ? (path) => path === value : (path) => new RegExp(value).test(path));
  return found.length ? (path) => found.some((test) => test(path)) : () => true;
}
const routes = table.routes.map((route) => ({ ...route, test: predicates(route.match) }));
const seconds = (period) => { const m = /^(\d+)(s|m)$/.exec(period); return Number(m[1]) * (m[2] === "m" ? 60 : 1); };
const buckets = new Map(), flights = new Map();

function admit(name, key, cleanups) {
  const middleware = table.middlewares[name];
  if (middleware.rateLimit) {
    const { average, period, burst } = middleware.rateLimit;
    const rate = average / seconds(period), now = Date.now() / 1000, id = `${name}|${key}`;
    const bucket = buckets.get(id) || { tokens: burst, at: now };
    bucket.tokens = Math.min(burst, bucket.tokens + (now - bucket.at) * rate); bucket.at = now;
    if (bucket.tokens < 1) { buckets.set(id, bucket); return false; }
    bucket.tokens -= 1; buckets.set(id, bucket);
  }
  if (middleware.inFlightReq) {
    const id = `${name}|${key}`, open = flights.get(id) || 0;
    if (open >= middleware.inFlightReq.amount) return false;
    flights.set(id, open + 1);
    cleanups.push(() => flights.set(id, (flights.get(id) || 1) - 1));
  }
  return true;
}

function securityHeaders(names) {
  const headers = {};
  for (const name of names) {
    const spec = table.middlewares[name].headers;
    if (!spec) continue;
    if (spec.stsSeconds) headers["strict-transport-security"] = `max-age=${spec.stsSeconds}`;
    if (spec.contentTypeNosniff) headers["x-content-type-options"] = "nosniff";
    if (spec.referrerPolicy) headers["referrer-policy"] = spec.referrerPolicy;
  }
  return headers;
}

createServer((incoming, outgoing) => {
  const path = new URL(incoming.url || "/", "http://localhost").pathname;
  const route = routes.find((item) => item.test(path));
  const extra = securityHeaders(route.middlewares);
  const key = incoming.headers["cf-connecting-ip"];
  const cleanups = [];
  const release = () => { while (cleanups.length) cleanups.pop()(); };
  if (key !== undefined) {
    for (const name of route.middlewares) {
      if ((table.middlewares[name].rateLimit || table.middlewares[name].inFlightReq) && !admit(name, key, cleanups)) {
        release();
        outgoing.writeHead(429, { "content-type": "text/plain; charset=utf-8", ...extra });
        return outgoing.end("Too Many Requests");
      }
    }
  }
  outgoing.on("close", release);
  const upstream = request({ hostname: "localhost", port: PORTS[route.upstream], path: incoming.url, method: incoming.method, headers: incoming.headers, timeout: 10_000 }, (response) => {
    outgoing.writeHead(response.statusCode || 503, { ...response.headers, ...extra }); response.pipe(outgoing);
  });
  upstream.on("error", () => { if (!outgoing.headersSent) outgoing.writeHead(503, extra); outgoing.end("Synthetic test upstream unavailable"); });
  upstream.on("timeout", () => upstream.destroy());
  incoming.on("aborted", () => upstream.destroy());
  incoming.pipe(upstream);
}).listen(4173);
