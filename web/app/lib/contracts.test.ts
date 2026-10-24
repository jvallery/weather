// SPDX-License-Identifier: MIT
import { expect, test } from "vitest";
import { catalogSchema, currentSchema, historySchema, intervalSchema, metricRegistrySchema } from "./contracts";
import current from "../fixtures/current.json";
import history from "../fixtures/history.json";
import catalog from "../fixtures/catalog.json";
import { ageLabel, localTime, metricValue } from "./format";
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
test("US conversions distinguish real zero from missing", () => {
  expect(metricValue(0, "degC", "us")).toBe("32 °F");
  expect(metricValue(null, "degC", "us")).toBe("—");
  expect(metricValue(0, "mm", "us")).toBe("0 in");
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
  const registry = { revision: "synthetic-registry", metrics: [{ metric: "temperature", label: "Temperature", canonical_unit: "degC", display_units: ["degC", "degF"], statistics: ["observed"], expected_interval_seconds: null, public: true, revision: "synthetic-metric" }] };
  expect(metricRegistrySchema.parse(registry).metrics[0].expected_interval_seconds).toBeNull();
  expect(metricRegistrySchema.safeParse({ ...registry, metrics: [{ ...registry.metrics[0], public: false }] }).success).toBe(false);
});
