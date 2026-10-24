// SPDX-License-Identifier: MIT
import { expect, test } from "vitest";
import { catalogSchema, currentSchema, historySchema, intervalSchema, metricRegistrySchema } from "./contracts";
import current from "../fixtures/current.json";
import history from "../fixtures/history.json";
import registryFixture from "../fixtures/metric-registry.json";
import catalog from "../fixtures/catalog.json";
import { ageLabel, conversionAvailable, localTime, metricValue } from "./format";
import { historySeries } from "../components/history-plot";

test("hand-typed public fixtures validate with zero, missing, DST and six camera segments", () => {
  expect(currentSchema.parse(current).metrics[0].value).toBe(0);
  expect(historySchema.parse(history).points[1].value).toBeNull();
  expect(catalogSchema.parse(catalog).items).toHaveLength(6);
});
test("private fields and provider URLs are rejected at the public boundary", () => {
  expect(currentSchema.safeParse({ ...current, station_serial: "SYNTHETIC-PRIVATE-FIELD" }).success).toBe(false);
  const polluted = structuredClone(catalog);
  polluted.items[0].renditions[0].asset.url = "https://example.invalid/original";
  expect(catalogSchema.safeParse(polluted).success).toBe(false);
});
test("fresh nulls, invented zero and malformed timestamps do not validate", () => {
  const missing = structuredClone(current);
  missing.metrics[0].value = null;
  expect(currentSchema.safeParse(missing).success).toBe(false);
  expect(currentSchema.safeParse({ ...current, generated_at: "2026-02-31T00:00:00Z" }).success).toBe(false);
  expect(intervalSchema.safeParse({ start: "2026-01-01T00:00:00.000001Z", end: "2026-01-01T00:00:00.000002Z" }).success).toBe(true);
});
test("expired products cannot smuggle asset pointers", () => {
  const expired = structuredClone(catalog);
  expired.items[0].state = "expired";
  expect(catalogSchema.safeParse(expired).success).toBe(false);
});
const registry = metricRegistrySchema.parse(registryFixture);
const definition = (metric: string) => registry.metrics.find((row) => row.metric === metric);
test("US conversions distinguish real zero from missing", () => {
  expect(metricValue(0, "degC", "us", definition("temperature"))).toBe("32 °F");
  expect(metricValue(null, "degC", "us")).toBe("—");
  expect(metricValue(0, "mm", "us", definition("precipitation"))).toBe("0 in");
});
test("repeated local hour carries a distinct numeric offset", () => {
  expect(localTime("2026-11-01T07:15:00Z")).toContain("GMT-06:00");
  expect(localTime("2026-11-01T08:15:00Z")).toContain("GMT-07:00");
});
test("source age keeps aging and plot gaps stay null", () => {
  const metric = currentSchema.parse(current).metrics[0];
  const label = ageLabel(metric, Date.parse("2026-11-01T09:15:00Z"));
  expect(label).toContain("1 h old"); expect(label).toContain("stale");
  const data = historySeries(historySchema.parse(history), "temperature");
  expect(data[1]).toContain(0);
  expect(data[1].filter((value) => value === null).length).toBeGreaterThanOrEqual(2);
});

test("uncommissioned source cadence is explicit null, never a guessed polling interval", () => {
  const value = currentSchema.parse(current); value.metrics[0].expected_interval_seconds = null;
  expect(currentSchema.parse(value).metrics[0].expected_interval_seconds).toBeNull();
  const registry = structuredClone(registryFixture);
  expect(metricRegistrySchema.parse(registry).metrics[0].expected_interval_seconds).toBeNull();
  expect(metricRegistrySchema.safeParse({ ...registry, metrics: [{ ...registry.metrics[0], public: false }] }).success).toBe(false);
});

test("required rational conversion recipes match display units and never guess without discovery", () => {
  expect(registry.metrics).toHaveLength(16);
  expect(metricValue(1, "m/s", "us", definition("wind_gust"))).toBe("2.2 mph");
  expect(metricValue(1000, "hPa", "us", definition("station_pressure"))).toBe("29.53 inHg");
  expect(metricValue(0, "degC", "us")).toBe("0 °C");
  expect(conversionAvailable("degC", "us")).toBe(false);
  const changed = structuredClone(registryFixture);
  changed.metrics[0].conversions[1].scale_denominator = 0;
  expect(metricRegistrySchema.safeParse(changed).success).toBe(false);
  expect(metricRegistrySchema.safeParse({ ...registryFixture, metrics: registryFixture.metrics.map((row) => ({ ...row, conversions: undefined })) }).success).toBe(false);
});
test("raw observations require zero resolution and canonical source units", () => {
  expect(historySchema.parse(history).resolution_seconds).toBe(0);
  expect(historySchema.safeParse({ ...history, resolution_seconds: 60 }).success).toBe(false);
  const changed = structuredClone(history); changed.points[0].unit = "degF";
  expect(historySchema.safeParse(changed).success).toBe(false);
  changed.points[0] = { ...history.points[0], observed_at: history.interval.end };
  expect(historySchema.safeParse(changed).success).toBe(false);
});
test("response clocks cannot freshen future/expired evidence and numeric invalidations stay null", () => {
  expect(currentSchema.safeParse({ ...current, generated_at: "2026-11-01T08:15:00Z" }).success).toBe(false);
  expect(currentSchema.safeParse({ ...current, generated_at: "2026-11-01T08:16:30Z" }).success).toBe(false);
  const changed = structuredClone(current); changed.metrics[0].quality = "invalid";
  expect(currentSchema.safeParse(changed).success).toBe(false);
  expect(currentSchema.safeParse({ ...current, metrics: [] }).success).toBe(false);
});
