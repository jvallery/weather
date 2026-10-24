// SPDX-License-Identifier: MIT
/**
 * Weather API types come only from the generated OpenAPI types (P12). Parsing is lenient: it
 * checks the fields the UI reads, ignores (and drops) fields it does not know, and leaves
 * business invariants to the server's Pydantic models.
 */
import { z } from "zod";
import type { components } from "../generated/weather-api";
// Zod's eval-capability probe violates a strict browser CSP; configure before building schemas.
z.config({ jitless: true });

type Schemas = components["schemas"];
export type Current = Schemas["Current"];
export type Metric = Schemas["Metric"];
export type MetricDefinition = Schemas["MetricDefinition"];
export type MetricRegistry = Schemas["MetricRegistry"];
export type Observations = Schemas["Observations"];
export type ObservationPoint = Schemas["ObservationPoint"];
export type Aggregates = Schemas["Aggregates"];
export type Aggregate = Schemas["Aggregate"];
export type Problem = Schemas["Problem"];

const text = z.string();
const number = z.number().nullable();
const coverage = z.object({ fraction: z.number(), support_seconds: z.number(), expected_seconds: z.number() });
const metric = z.object({
  metric: text, value: number, unit: text, observed_at: text.nullable(), received_at: text.nullable(),
  expected_interval_seconds: number.default(null), valid_until: text.nullable().default(null),
  quality: z.enum(["valid", "missing", "invalid"]), freshness: z.enum(["fresh", "stale", "unavailable"]),
  reason: text.nullable().default(null), coverage: number.default(null),
});
const current = z.object({
  schema_version: z.literal(1), generated_at: text, station_alias: text,
  data_state: z.enum(["live", "cached", "unavailable"]), metrics: z.array(metric),
});
const conversion = z.object({ unit: text, scale_numerator: z.number(), scale_denominator: z.number(), offset_numerator: z.number(), offset_denominator: z.number() });
const definition = z.object({
  metric: text, label: text, canonical_unit: text, conversions: z.array(conversion),
  statistics: z.array(text), expected_interval_seconds: number.default(null),
});
const registry = z.object({ metrics: z.array(definition) });
const point = z.object({ id: text, observed_at: text, received_at: text, metric: text, value: number, unit: text, quality: z.enum(["valid", "missing", "invalid"]) });
const observations = z.object({ generated_at: text, start: text, end: text, metrics: z.array(text), points: z.array(point), next_cursor: text.nullable() });
const rose = z.object({
  category: z.enum(["directional", "calm", "variable", "missing"]), sector_start: number, sector_end: number,
  speed_min: number, speed_max: number, duration_seconds: z.number(),
});
const aggregate = z.object({
  metric: text, statistic: text, start: text, end: text, value: number, unit: text, coverage,
  evidence_count: z.number(), evidence_hash: text.nullable().default(null), reason: text.nullable().default(null),
  direction_state: z.enum(["directional", "variable", "calm", "missing"]).nullable().default(null),
  rose: z.array(rose).nullable().default(null),
});
const aggregates = z.object({ generated_at: text, start: text, end: text, resolution: text, items: z.array(aggregate) });

// The parsed shapes must stay assignable to the generated types; tsc fails on drift.
const checks: [
  (value: z.infer<typeof current>) => Current,
  (value: z.infer<typeof registry>) => MetricRegistry,
  (value: z.infer<typeof observations>) => Observations,
  (value: z.infer<typeof aggregates>) => Aggregates,
] = [(value) => value, (value) => value, (value) => value, (value) => value];
void checks;

export type Parser<T> = (value: unknown) => T | null;
export function parser<T>(schema: z.ZodType<T>): Parser<T> {
  return (value) => { const result = schema.safeParse(value); return result.success ? result.data : null; };
}
export const parseCurrent: Parser<Current> = parser(current);
export const parseRegistry: Parser<MetricRegistry> = parser(registry);
export const parseObservations: Parser<Observations> = parser(observations);
export const parseAggregates: Parser<Aggregates> = parser(aggregates);
