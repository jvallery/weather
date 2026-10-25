// SPDX-License-Identifier: MIT
import { expect, test } from "vitest";
import { intervalSchema } from "./contracts";
import { parseAggregates, parseCurrent, parseRegistry, parseTimelapses, playable } from "./weather-api";
import current from "../fixtures/current.json";
import aggregatesFixture from "../fixtures/aggregates.json";
import registryFixture from "../fixtures/metric-registry.json";
import catalog from "../fixtures/catalog.json";
import { ageLabel, conversionAvailable, localTime, metricValue, plotTime } from "./format";
import { historyRows, historySeries } from "../components/history-plot";

const registry = parseRegistry(registryFixture)!;
const definition = (metric: string) => registry.metrics.find((row) => row.metric === metric);

test("generated-type fixtures parse, keeping zero distinct from missing", () => {
  expect(parseCurrent(current)?.metrics[0].value).toBe(0);
  expect(parseAggregates(aggregatesFixture)?.items).toHaveLength(12);
  expect(registry.metrics).toHaveLength(16);
  expect(parseTimelapses(catalog)?.items).toHaveLength(7);
});
test("parsing is lenient about additive fields and drops what the UI does not know", () => {
  const extended = { ...current, future_field: 1, metrics: current.metrics.map((metric) => ({ ...metric, future_metric_field: "x" })) };
  const parsed = parseCurrent(extended);
  expect(parsed?.metrics).toHaveLength(3);
  expect(JSON.stringify(parsed)).not.toContain("future");
  const older = { ...current, metrics: current.metrics.map((metric) => { const copy: Record<string, unknown> = { ...metric }; delete copy.coverage; return copy; }) };
  expect(parseCurrent(older)?.metrics[0].coverage).toBeNull();
  const widened = { ...current, data_state: "degraded", metrics: current.metrics.map((metric) => ({ ...metric, quality: "estimated", freshness: "aging" })) };
  const lenient = parseCurrent(widened);
  expect(lenient?.data_state).toBe("cached");
  expect(lenient?.metrics.map((metric) => [metric.quality, metric.freshness])[0]).toEqual(["missing", "stale"]);
  const item = { ...aggregatesFixture.items[0], direction_state: "gusty", rose: [{ category: "squall", sector_start: null, sector_end: null, speed_min: null, speed_max: null, duration_seconds: 1 }] };
  const parsed2 = parseAggregates({ ...aggregatesFixture, items: [item] });
  expect(parsed2?.items[0].direction_state).toBeNull();
  expect(parsed2?.items[0].rose?.[0].category).toBe("missing");
});
test("responses missing what the UI reads are rejected", () => {
  expect(parseCurrent({ ...current, metrics: undefined })).toBeNull();
  expect(parseCurrent({ ...current, metrics: [{ ...current.metrics[0], value: "0" }] })).toBeNull();
  expect(parseAggregates({ ...aggregatesFixture, items: [{ metric: "temperature" }] })).toBeNull();
  expect(intervalSchema.safeParse({ start: "2026-01-01T00:00:00.000001Z", end: "2026-01-01T00:00:00.000002Z" }).success).toBe(true);
});
test("provider URLs never reach the page: the asset is dropped and the entry is not playable", () => {
  const polluted = structuredClone(catalog);
  polluted.items[0].renditions[0].asset.url = "https://example.invalid/original";
  (polluted.items[1] as { keogram_state: string }).keogram_state = "galaxy";
  (polluted.items[2] as Record<string, unknown>).private_object_key = "originals/private";
  const parsed = parseTimelapses(polluted)!;
  expect(parsed.items[0].renditions).toHaveLength(2);
  expect(playable(parsed.items[0])).toBe(false);
  expect(parsed.items[1].keogram_state).toBe("unavailable");
  expect(JSON.stringify(parsed)).not.toMatch(/example\.invalid|private_object_key/);
  // One malformed entry is dropped, never the page.
  expect(parseTimelapses({ ...catalog, items: [{ video_id: "x" }, ...catalog.items] })?.items).toHaveLength(7);
  expect(parseTimelapses({ ...catalog, items: "nope" })).toBeNull();
});
test("US conversions distinguish real zero from missing", () => {
  expect(metricValue(0, "degC", "us", definition("temperature"))).toBe("32 °F");
  expect(metricValue(null, "degC", "us")).toBe("—");
  expect(metricValue(0, "mm", "us", definition("precipitation"))).toBe("0 in");
  expect(metricValue(1, "m/s", "us", definition("wind_gust"))).toBe("2.2 mph");
  expect(metricValue(1000, "hPa", "us", definition("station_pressure"))).toBe("29.53 inHg");
  expect(metricValue(0, "degC", "us")).toBe("0 °C");
  expect(conversionAvailable("degC", "us")).toBe(false);
});
test("repeated local hour carries a distinct numeric offset", () => {
  expect(localTime("2026-11-01T07:15:00Z")).toContain("GMT-06:00");
  expect(localTime("2026-11-01T08:15:00Z")).toContain("GMT-07:00");
  expect(plotTime(Date.parse("2026-11-01T07:15:00Z") / 1000)).toBe("11/01/2026\n01:15 GMT-06:00");
  expect(plotTime(Date.parse("2026-11-01T08:15:00Z") / 1000)).toBe("11/01/2026\n01:15 GMT-07:00");
});
test("source age keeps aging and aggregate gaps stay null, never zero", () => {
  const metric = parseCurrent(current)!.metrics[0];
  const label = ageLabel(metric, Date.parse("2026-11-01T09:15:00Z"));
  expect(label).toContain("1 h old"); expect(label).toContain("stale");
  const rows = historyRows(parseAggregates(aggregatesFixture)!, "temperature", ["mean", "min", "max"]);
  expect(rows).toHaveLength(4);
  const [times, means] = historySeries(rows, ["mean", "min", "max"]);
  expect(times).toHaveLength(4);
  expect(means).toEqual([0, null, 2, 1.5]);
  expect(rows[1].notes.mean).toBe("No valid observations in this interval.");
});
