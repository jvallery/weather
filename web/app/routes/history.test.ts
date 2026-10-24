// SPDX-License-Identifier: MIT
import { afterEach, expect, test, vi } from "vitest";
import { chartStatistics, loader, resolutionFor } from "./history";
import aggregates from "../fixtures/aggregates.json";
import registry from "../fixtures/metric-registry.json";
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

test("the default 24 h view is one aggregate request, never a truncated raw page", async () => {
  vi.stubEnv("API_ORIGIN", "http://api");
  const urls: URL[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: URL) => { urls.push(url); return new Response(JSON.stringify(url.pathname.endsWith("metrics") ? registry : aggregates)); }));
  const data = await loader({ request: new Request("https://weather.example/weather/history/"), params: {}, context: {} } as Parameters<typeof loader>[0]);
  const read = urls.find((url) => url.pathname === "/api/v1/weather/aggregates")!;
  expect(read.searchParams.get("resolution")).toBe("300");
  expect(read.searchParams.getAll("statistics")).toEqual(["mean", "min", "max"]);
  expect(urls.some((url) => url.pathname.endsWith("/observations"))).toBe(false);
  expect(data.aggregates?.items).toHaveLength(12);
});
test("statistics and resolution follow the metric and the window", () => {
  expect(chartStatistics({ metric: "precipitation", label: "Rain", canonical_unit: "mm", conversions: [], statistics: ["observed", "sum"], expected_interval_seconds: 60 })).toEqual(["sum"]);
  expect(chartStatistics({ metric: "rain_today", label: "Rain today", canonical_unit: "mm", conversions: [], statistics: [], expected_interval_seconds: 60 })).toEqual([]);
  expect([resolutionFor(86_400_000), resolutionFor(5 * 86_400_000), resolutionFor(31 * 86_400_000)]).toEqual(["300", "3600", "10800"]);
});
