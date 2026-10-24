// SPDX-License-Identifier: MIT
import { z } from "zod";
import { data } from "react-router";
import { Counter, Gauge, register } from "prom-client";
import { catalogSchema } from "./contracts";
import { parseAggregates, parseAlmanac, parseArchive, parseCurrent, parseEvents, parser, parseRegistry, type Current, type Parser } from "./weather-api";

const ALLOWED = /^\/api\/v1\/(?:weather\/(?:current|observations|aggregates|almanac|metrics)|events|timelapses(?:\/[A-Za-z0-9_-]{1,128})?)$/;
const parseCatalog = parser(catalogSchema);
const requests = (register.getSingleMetric("weather_web_api_requests_total") as Counter<"route" | "result"> | undefined) || new Counter({ name: "weather_web_api_requests_total", help: "Bounded SSR public API reads by result", labelNames: ["route", "result"] });
const inFlight = (register.getSingleMetric("weather_web_api_inflight") as Gauge | undefined) || new Gauge({ name: "weather_web_api_inflight", help: "Active bounded SSR API reads" });
let active = 0;
// Three reads per admitted SSR document (Today) across server.mjs's four slots.
const API_SLOTS = 12;
/** Overload is "busy, retry", never "API unavailable": the root boundary renders it with Retry-After. */
export function busy() { return data("busy", { status: 503, headers: { "Retry-After": "5" } }); }
function origin(): string {
  const value = new URL(process.env.API_ORIGIN || "http://api");
  const production = value.protocol === "http:" && value.hostname === "api" && value.port === "";
  const fixture = process.env.WEATHER_CI_IN_CLUSTER === "1" && value.protocol === "http:" && value.hostname === "localhost";
  if ((!production && !fixture) || value.username || value.password || value.search || value.hash || value.pathname !== "/") throw new Error("Invalid API upstream configuration");
  return value.origin;
}
const problemSchema = z.object({ code: z.string().max(64), suggested_resolution_seconds: z.number().int().positive().nullable().default(null) });
export type InputIssue = { code: "invalid_request" | "unknown_metric" | "unsupported_statistic" | "unsupported_unit" | "invalid_cursor" | "range_too_large"; suggestedResolutionSeconds: number | null };
export type PublicResult<T> = { kind: "ok"; data: T } | { kind: "invalid"; issue: InputIssue } | { kind: "unavailable" };
export async function publicReadResult<T>(path: string, parse: Parser<T>, requestSignal: AbortSignal, params = new URLSearchParams()): Promise<PublicResult<T>> {
  const route = path.startsWith("/api/v1/timelapses/") ? "/api/v1/timelapses/:id" : path;
  if (!ALLOWED.test(path)) { requests.labels("other", "invalid_response").inc(); return { kind: "unavailable" }; }
  if (active >= API_SLOTS) { requests.labels(route, "overloaded").inc(); throw busy(); }
  active += 1; inFlight.inc();
  try {
    const target = new URL(path, origin());
    target.search = params.toString();
    const response = await fetch(target, { signal: AbortSignal.any([requestSignal, AbortSignal.timeout(6000)]), redirect: "error", headers: { Accept: "application/json" } });
    if ((!response.ok && response.status !== 400) || !response.body) { requests.labels(route, "http_error").inc(); return { kind: "unavailable" }; }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let bytes = 0;
    let body = "";
    try {
      for (;;) {
        const result = await reader.read();
        if (result.done) break;
        bytes += result.value.byteLength;
        if (bytes > 4 * 1024 * 1024) { requests.labels(route, "too_large").inc(); await reader.cancel(); return { kind: "unavailable" }; }
        body += decoder.decode(result.value, { stream: true });
      }
      body += decoder.decode();
    } finally { reader.releaseLock(); }
    const json: unknown = JSON.parse(body);
    if (response.status === 400) {
      const problem = problemSchema.safeParse(json);
      const accepted = ["invalid_request", "unknown_metric", "unsupported_statistic", "unsupported_unit", "invalid_cursor", "range_too_large"];
      requests.labels(route, "input_error").inc();
      return { kind: "invalid", issue: { code: problem.success && accepted.includes(problem.data.code) ? problem.data.code as InputIssue["code"] : "invalid_request", suggestedResolutionSeconds: problem.success ? problem.data.suggested_resolution_seconds : null } };
    }
    const parsed = parse(json);
    requests.labels(route, parsed !== null ? "ok" : "invalid_response").inc();
    return parsed !== null ? { kind: "ok", data: parsed } : { kind: "unavailable" };
  } catch (error) { requests.labels(route, error instanceof SyntaxError ? "invalid_response" : "network_error").inc(); return { kind: "unavailable" }; }
  finally { active -= 1; inFlight.dec(); }
}
export async function publicRead<T>(path: string, parse: Parser<T>, requestSignal: AbortSignal, params = new URLSearchParams()): Promise<T | null> {
  const result = await publicReadResult(path, parse, requestSignal, params);
  return result.kind === "ok" ? result.data : null;
}
export function unavailableCurrent(now: number): Current {
  return { schema_version: 1, generated_at: new Date(now).toISOString(), station_alias: "Local station", data_state: "unavailable", metrics: [] };
}
export async function currentRead(request: Request) {
  const current = await publicRead("/api/v1/weather/current", parseCurrent, request.signal);
  const now = Date.now();
  return { current: current || unavailableCurrent(now), now, apiUnavailable: current === null };
}
export async function catalogRead(request: Request, extra = new URLSearchParams()) {
  extra.set("limit", "100");
  return publicRead("/api/v1/timelapses", parseCatalog, request.signal, extra);
}
export async function aggregatesRead(request: Request, params: URLSearchParams) {
  return publicReadResult("/api/v1/weather/aggregates", parseAggregates, request.signal, params);
}
/** The archive month page: its month (or other filters) and at most 100 entries per page. */
export async function archiveRead(request: Request, params: URLSearchParams) {
  const query = new URLSearchParams(params);
  query.set("limit", "100");
  return publicRead("/api/v1/timelapses", parseArchive, request.signal, query);
}

export async function almanacRead(request: Request, params: URLSearchParams) {
  return publicReadResult("/api/v1/weather/almanac", parseAlmanac, request.signal, params);
}
export async function metricRegistryRead(request: Request) {
  return publicRead("/api/v1/weather/metrics", parseRegistry, request.signal);
}
export async function eventsRead(request: Request, params: URLSearchParams) {
  return publicRead("/api/v1/events", parseEvents, request.signal, params);
}
