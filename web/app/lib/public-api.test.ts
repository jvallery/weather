// SPDX-License-Identifier: MIT
import { afterEach, expect, test, vi } from "vitest";
import { publicRead } from "./public-api.server";
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
