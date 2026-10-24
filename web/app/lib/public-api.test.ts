// SPDX-License-Identifier: MIT
import { afterEach, expect, test, vi } from "vitest";
import { currentRead, historyRead, publicRead } from "./public-api.server";
import { currentSchema } from "./contracts";
import fixture from "../fixtures/current.json";
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });
test("only the fixed API upstream is used; visitor URLs never become targets", async () => {
  vi.stubEnv("API_ORIGIN", "https://example.invalid/private");
  const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
  expect(await publicRead("/api/v1/weather/current", currentSchema, new AbortController().signal)).toBeNull();
  expect(fetch).not.toHaveBeenCalled();
});
test("upstream invalid or private fields are rejected without returning internal errors", async () => {
  vi.stubEnv("API_ORIGIN", "http://api");
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ ...fixture, private_notes: "synthetic private marker" }))));
  expect(await publicRead("/api/v1/weather/current", currentSchema, new AbortController().signal)).toBeNull();
});
test("concurrent requests retain their own result and receive no cookie forwarding", async () => {
  vi.stubEnv("API_ORIGIN", "http://api");
  const fetch = vi.fn().mockImplementationOnce(async () => new Response(JSON.stringify({ ...fixture, station_alias: "synthetic-A" }))).mockImplementationOnce(async () => new Response(JSON.stringify({ ...fixture, station_alias: "synthetic-B" })));
  vi.stubGlobal("fetch", fetch);
  const results = await Promise.all([publicRead("/api/v1/weather/current", currentSchema, new AbortController().signal), publicRead("/api/v1/weather/current", currentSchema, new AbortController().signal)]);
  expect(results.map((result) => result?.station_alias)).toEqual(["synthetic-A", "synthetic-B"]);
  expect(fetch.mock.calls.every((call) => Object.keys(call[1].headers).join() === "Accept")).toBe(true);
});

test("SSR API admission is bounded and releases capacity after completion", async () => {
  vi.stubEnv("API_ORIGIN", "http://api");
  const complete: Array<(response: Response) => void> = [];
  const fetch = vi.fn(() => new Promise<Response>((resolve) => complete.push(resolve)));
  vi.stubGlobal("fetch", fetch);
  const work = Array.from({ length: 6 }, () => publicRead("/api/v1/weather/current", currentSchema, new AbortController().signal));
  expect(fetch).toHaveBeenCalledTimes(4);
  for (const resolve of complete) resolve(new Response(JSON.stringify(fixture)));
  expect((await Promise.all(work)).filter(Boolean)).toHaveLength(4);
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(fixture))));
  expect(await publicRead("/api/v1/weather/current", currentSchema, new AbortController().signal)).not.toBeNull();
});

test("bounded history input errors keep only safe codes and resolution suggestions", async () => {
  vi.stubEnv("API_ORIGIN", "http://api");
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ code: "range_too_large", message: "synthetic private diagnostic", request_id: "synthetic-private-id", suggested_resolution_seconds: 3600 }), { status: 400 })));
  const result = await historyRead(new Request("https://weather.example/history"), new URLSearchParams());
  expect(result).toEqual({ kind: "invalid", issue: { code: "range_too_large", suggestedResolutionSeconds: 3600 } });
  expect(JSON.stringify(result)).not.toContain("private");
});
test("a successful unavailable response is distinct from an upstream transport failure", async () => {
  vi.stubEnv("API_ORIGIN", "http://api");
  const unavailable = { ...fixture, data_state: "unavailable", metrics: [] };
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(unavailable))));
  expect((await currentRead(new Request("https://weather.example"))).apiUnavailable).toBe(false);
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 503 })));
  expect((await currentRead(new Request("https://weather.example"))).apiUnavailable).toBe(true);
});
