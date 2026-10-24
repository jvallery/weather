// SPDX-License-Identifier: MIT
import type { z } from "zod";
import { catalogSchema, currentSchema, historySchema, type Current } from "./contracts";

const ALLOWED = /^\/api\/v1\/(?:weather\/(?:current|observations|metrics)|timelapses(?:\/[A-Za-z0-9_-]{1,128})?)$/;
let active = 0;
function origin(): string {
  const value = new URL(process.env.API_ORIGIN || "http://api");
  const production = value.protocol === "http:" && value.hostname === "api" && value.port === "";
  const fixture = process.env.WEATHER_CI_IN_CLUSTER === "1" && value.protocol === "http:" && value.hostname === "localhost";
  if ((!production && !fixture) || value.username || value.password || value.search || value.hash || value.pathname !== "/") throw new Error("Invalid API upstream configuration");
  return value.origin;
}
export async function publicRead<T>(path: string, schema: z.ZodType<T>, requestSignal: AbortSignal, params = new URLSearchParams()): Promise<T | null> {
  if (!ALLOWED.test(path) || active >= 16) return null;
  active += 1;
  try {
    const target = new URL(path, origin());
    target.search = params.toString();
    const response = await fetch(target, { signal: AbortSignal.any([requestSignal, AbortSignal.timeout(6000)]), redirect: "error", headers: { Accept: "application/json" } });
    if (!response.ok || !response.body) return null;
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let bytes = 0;
    let body = "";
    try {
      for (;;) {
        const result = await reader.read();
        if (result.done) break;
        bytes += result.value.byteLength;
        if (bytes > 8 * 1024 * 1024) { await reader.cancel(); return null; }
        body += decoder.decode(result.value, { stream: true });
      }
      body += decoder.decode();
    } finally { reader.releaseLock(); }
    const parsed = schema.safeParse(JSON.parse(body));
    return parsed.success ? parsed.data : null;
  } catch { return null; }
  finally { active -= 1; }
}
export function unavailableCurrent(now: number): Current {
  return { schema_version: 1, generated_at: new Date(now).toISOString(), station_alias: "Local station", data_state: "unavailable", metrics: [] };
}
export async function currentRead(request: Request) {
  const current = await publicRead("/api/v1/weather/current", currentSchema, request.signal);
  const now = Date.now();
  return { current: current || unavailableCurrent(now), now };
}
export async function catalogRead(request: Request, extra = new URLSearchParams()) {
  extra.set("limit", "100");
  return publicRead("/api/v1/timelapses", catalogSchema, request.signal, extra);
}
export async function historyRead(request: Request, params: URLSearchParams) {
  return publicRead("/api/v1/weather/observations", historySchema, request.signal, params);
}
