// SPDX-License-Identifier: MIT
/**
 * Weather API types come only from the generated OpenAPI types (P12). Parsing is lenient: it
 * checks the fields the UI reads, ignores (and drops) fields it does not know, maps an enum value
 * it does not know to the most conservative known one, and leaves business invariants to the
 * server's Pydantic models.
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
export type Aggregates = Schemas["Aggregates"];
export type Aggregate = Schemas["Aggregate"];
export type Problem = Schemas["Problem"];
export type Almanac = Schemas["Almanac"];
export type AlmanacItem = Almanac["items"][number];
export type RecordItem = Schemas["RecordItem"];
export type RainDayItem = Schemas["RainDayItem"];
export type WindRoseBinItem = Schemas["WindRoseBinItem"];
export type AstronomyItem = Schemas["AstronomyItem"];
export type ComparisonItem = Schemas["ComparisonItem"];
export type ComparisonWindow = Schemas["ComparisonWindow"];
// The archive month page renders only these fields of each /api/v1/timelapses entry.
export type Timelapse = Schemas["Timelapse"];
export type TimelapsePage = Schemas["TimelapsePage"];
export type Asset = Schemas["Asset"];
export type ArchiveEntry = Pick<Timelapse, "video_id" | "camera" | "cycle_date" | "segment" | "state" | "reason">;
export type ArchivePage = { items: ArchiveEntry[]; next_cursor: string | null };
export type WeatherEvent = Schemas["Event"];
export type Events = Schemas["Events"];
export type EventVideo = Schemas["EventVideo"];
export type Still = Schemas["Still"];
export type StillImage = Schemas["StillImage"];
export type StillPage = Schemas["StillPage"];

const text = z.string();
// A new server enum value degrades that one field instead of rejecting the whole response.
const lenient = <const T extends readonly [string, ...string[]]>(values: T, fallback: T[number]) => z.enum(values).catch(fallback);
const number = z.number().nullable();
const coverage = z.object({ fraction: z.number(), support_seconds: z.number(), expected_seconds: z.number() });
const metric = z.object({
  metric: text, value: number, unit: text, observed_at: text.nullable(), received_at: text.nullable(),
  expected_interval_seconds: number.default(null), valid_until: text.nullable().default(null),
  quality: lenient(["valid", "missing", "invalid"], "missing"), freshness: lenient(["fresh", "stale", "unavailable"], "stale"),
  reason: text.nullable().default(null), coverage: number.default(null),
});
const current = z.object({
  schema_version: z.literal(1), generated_at: text, station_alias: text,
  data_state: lenient(["live", "cached", "unavailable"], "cached"), metrics: z.array(metric),
});
const conversion = z.object({ unit: text, scale_numerator: z.number(), scale_denominator: z.number(), offset_numerator: z.number(), offset_denominator: z.number() });
const definition = z.object({
  metric: text, label: text, canonical_unit: text, conversions: z.array(conversion),
  statistics: z.array(text), expected_interval_seconds: number.default(null),
});
const registry = z.object({ metrics: z.array(definition) });
const rose = z.object({
  category: lenient(["directional", "calm", "variable", "missing"], "missing"), sector_start: number, sector_end: number,
  speed_min: number, speed_max: number, duration_seconds: z.number(),
});
const aggregate = z.object({
  metric: text, statistic: text, start: text, end: text, value: number, unit: text, coverage,
  evidence_count: z.number(), evidence_hash: text.nullable().default(null), reason: text.nullable().default(null),
  direction_state: z.enum(["directional", "variable", "calm", "missing"]).nullable().default(null).catch(null),
  rose: z.array(rose).nullable().default(null),
});
const aggregates = z.object({ generated_at: text, start: text, end: text, resolution: text, items: z.array(aggregate) });
// Almanac (#42): a reason code the UI does not know yet is dropped rather than failing the page.
type AlmanacReason = Schemas["RecordItem"]["missing_reasons"][number];
const REASONS = ["no_observations", "partial_coverage", "below_record_coverage", "before_history", "inadequate_coverage", "site_not_configured", "solar_events_unavailable", "no_moonrise", "no_moonset"] as const satisfies readonly AlmanacReason[];
const reasons = z.array(z.string()).transform((values) => values.filter((value): value is AlmanacReason => (REASONS as readonly string[]).includes(value)));
const interval = z.object({ start: text, end: text });
const instant = text.nullable();
const day = { local_date: text, start: text, end: text };
const observed = { value: number, coverage, evidence_count: z.number(), evidence_hash: text.nullable().default(null), missing_reasons: reasons };
const recordItem = z.object({ product: z.literal("record"), ...day, ...observed, ranking_eligible: z.boolean(), rank: number });
const rainDayItem = z.object({ product: z.literal("rain_day"), ...day, ...observed, complete: z.boolean() });
const roseItem = z.object({
  product: z.literal("wind_rose_bin"), category: lenient(["directional", "calm", "variable", "missing"], "missing"),
  sector_start: number, sector_end: number, speed_min: number, speed_max: number, duration_seconds: z.number(), denominator_seconds: z.number(),
});
const astronomyItem = z.object({
  product: z.literal("astronomy"), ...day, civil_dawn: instant, sunrise: instant, solar_noon: instant, sunset: instant, civil_dusk: instant,
  day_length_seconds: number, moon_phase: number,
  moon_phase_name: z.enum(["new", "waxing_crescent", "first_quarter", "waxing_gibbous", "full", "waning_gibbous", "last_quarter", "waning_crescent"]).nullable().catch(null),
  moonrise: instant, moonset: instant, missing_reasons: reasons,
});
const comparisonWindow = z.object({
  start: text, end: text, duration_seconds: z.number(), ...observed, adequate: z.boolean(), missing_days: z.array(text), partial_days: z.array(text),
});
const comparisonItem = z.object({ product: z.literal("comparison"), first: comparisonWindow, second: comparisonWindow, difference: number, reason: z.enum(REASONS).nullable().catch(null) });
// Items of an unknown future product are dropped, not fatal.
const almanacItem = z.union([recordItem, rainDayItem, roseItem, astronomyItem, comparisonItem]).nullable().catch(null);
const almanac = z.object({
  generated_at: text, product: z.enum(["record", "rain_day", "wind_rose_bin", "astronomy", "comparison"]), interval,
  comparison_interval: interval.nullable().default(null), history_interval: interval.nullable(), timezone: z.literal("America/Denver"),
  label: text, derived: z.boolean(), metric: text.nullable(), statistic: text.nullable(), unit: text.nullable(),
  formula_revision: text, library_revision: text, evidence_count: z.number(), evidence_hash: text.nullable().default(null),
  coverage: coverage.nullable().default(null), missing_reasons: reasons, next_cursor: text.nullable().default(null),
  items: z.array(almanacItem).transform((items) => items.filter((item) => item !== null)),
});
const archiveEntry = z.object({
  video_id: text, camera: text, cycle_date: text, segment: z.enum(["daytime", "overnight"]),
  state: lenient(["published", "partial", "unavailable", "withdrawn"], "unavailable"), reason: text.nullable().default(null),
});
const archive = z.object({ items: z.array(archiveEntry), next_cursor: text.nullable() });
// Full catalog entries (#18) for the day, video and Today pages. Media URLs must be same-origin
// opaque paths; an asset that is not is dropped, so the entry degrades instead of linking out.
const media = z.object({
  asset_id: text, class: z.enum(["video", "poster", "keogram", "sidecar"]), url: text.regex(/^\/media\/[0-9a-f-]{36}$/),
  mime: text, bytes: z.number(), width: number, height: number,
});
const optionalMedia = media.nullable().catch(null).default(null);
const rendition = z.object({ role: z.enum(["native", "web", "mobile"]), asset: media });
const timelapse = z.object({
  schema_version: z.literal(1), video_id: text, camera: text, cycle_date: text, segment: z.enum(["daytime", "overnight"]),
  state: lenient(["published", "partial", "unavailable", "withdrawn"], "unavailable"), reason: text.nullable().default(null),
  withdrawn: z.boolean().default(false), revision: z.number(), updated_at: text, source_interval: interval, allocated_frames: z.number(),
  renditions: z.array(rendition.nullable().catch(null)).transform((items) => items.filter((item) => item !== null)),
  poster: optionalMedia, sidecar: optionalMedia, keogram: optionalMedia,
  native_download_url: text.regex(/^\/downloads\/[0-9a-f-]{36}\/native$/).nullable().catch(null).default(null),
  keogram_state: lenient(["pending", "published", "partial", "unavailable", "withdrawn"], "unavailable"),
});
// One malformed entry is dropped, never the page.
const timelapses = z.object({
  generated_at: text, next_cursor: text.nullable().default(null),
  items: z.array(timelapse.nullable().catch(null)).transform((items) => items.filter((item) => item !== null)),
});

const camera = z.enum(["skycam_east", "skycam_west", "frontyard_1"]);
const video = z.object({
  camera, state: lenient(["available", "unavailable"], "unavailable"), video_id: text.nullable().default(null),
  cycle_date: text.nullable().default(null), segment: z.enum(["daytime", "overnight"]).nullable().default(null).catch(null),
  seek: z.object({ start_seconds: z.number(), end_seconds: z.number(), source_interval: interval }).nullable().default(null),
});
// An unknown kind or origin is shown with the most cautious label, never as a measurement.
const weatherEvent = z.object({
  id: text, revision: z.number(), label: text, human_edited: z.boolean(), rule_revision: text,
  kind: lenient(["precipitation_onset", "daily_gust_max", "lightning_strike", "lightning_interval", "visual", "synthesis", "manual"], "synthesis"),
  origin: lenient(["measured", "visual", "synthesis", "manual"], "synthesis"), basis: lenient(["instrument", "visual", "inference"], "inference"),
  precision: lenient(["second", "interval", "clip"], "clip"), source_interval: interval, camera: camera.nullable().catch(null),
  local_dates: z.array(text), evidence_ids: z.array(text), gust_mps: number.default(null), strikes: number.default(null),
  distance_km: number.default(null), coverage: coverage.nullable().default(null), videos: z.array(video).default([]), updated_at: text,
});
const events = z.object({ generated_at: text, items: z.array(weatherEvent), next_cursor: text.nullable().default(null) });
// Hourly stills (#38): an image of an unknown type is dropped; an unknown state shows as pending.
const stillImage = z.object({
  asset_id: text, url: z.string().regex(/^\/media\/[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/), mime: z.enum(["image/jpeg", "image/webp"]),
  bytes: z.number(), width: z.number(), height: z.number(),
});
const still = z.object({
  schema_version: z.literal(1), camera, slot_id: text, day_id: text, calendar_date: text, cycle_date: text.nullable(),
  segment: z.enum(["daytime", "overnight"]).nullable().catch(null), local_label: text, utc_offset_minutes: z.number(), fold: z.number(),
  slot_interval: interval, state: lenient(["on_target", "off_target", "missing", "pending", "withdrawn"], "pending"),
  reason: text.nullable(), policy: text.nullable(), revision: z.number().nullable(), source_interval: interval.nullable(),
  offset_seconds: z.number().nullable(),
  images: z.array(stillImage.nullable().catch(null)).transform((items) => items.filter((item) => item !== null)),
});
const stills = z.object({ generated_at: text, items: z.array(still) });

// The parsed shapes must stay assignable to the generated types; tsc fails on drift.
const checks: [
  (value: z.infer<typeof current>) => Current,
  (value: z.infer<typeof registry>) => MetricRegistry,
  (value: z.infer<typeof aggregates>) => Aggregates,
  (value: z.infer<typeof almanac>) => Almanac,
  (value: z.infer<typeof archive>) => ArchivePage,
  (value: z.infer<typeof events>) => Events,
  (value: z.infer<typeof timelapses>) => TimelapsePage,
  (value: z.infer<typeof stills>) => StillPage,
] = [(value) => value, (value) => value, (value) => value, (value) => value, (value) => value, (value) => value, (value) => value, (value) => value];
void checks;

export type Parser<T> = (value: unknown) => T | null;
export function parser<T>(schema: z.ZodType<T>): Parser<T> {
  return (value) => { const result = schema.safeParse(value); return result.success ? result.data : null; };
}
export const parseCurrent: Parser<Current> = parser(current);
export const parseRegistry: Parser<MetricRegistry> = parser(registry);
export const parseAggregates: Parser<Aggregates> = parser(aggregates);
export const parseAlmanac: Parser<Almanac> = parser(almanac);
export const parseArchive: Parser<ArchivePage> = parser(archive);
export const parseEvents: Parser<Events> = parser(events);
export const parseTimelapses: Parser<TimelapsePage> = parser(timelapses);

/** A playable entry: published or partial with the full media set (partial plays with a label, C6). */
export function playable(video: Timelapse): boolean {
  const roles = new Set(video.renditions.map((item) => item.role));
  return (video.state === "published" || video.state === "partial") && !video.withdrawn && roles.size === 3 && video.poster !== null && video.native_download_url !== null;
}
export const parseStills: Parser<StillPage> = parser(stills);
