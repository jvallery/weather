// SPDX-License-Identifier: MIT
/** Strict runtime boundary for the code-first public API; media contracts remain independent. */
import { z } from "zod";

const text = z.string().min(1).max(512);
const id = z.string().regex(/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/);
const utc = z.string().regex(/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?Z$/).refine((value) => {
  const parsed = new Date(value);
  return Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 19) === value.slice(0, 19);
}, "Invalid UTC instant");
const date = z.string().regex(/^\d{4}-\d\d-\d\d$/).refine((value) => {
  const parsed = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
}, "Invalid calendar date");
const number = z.number().finite();
const nonnegative = number.nonnegative();
const integer = z.number().int().nonnegative();
const reason = text.nullable();
export const cameraSchema = z.enum(["skycam_east", "skycam_west", "frontyard_1"]);
export const segmentSchema = z.enum(["daytime", "overnight"]);
function sourceMicros(value: string): bigint | null {
  const match = /^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d{1,6}))?Z$/.exec(value);
  if (!match) return null;
  const whole = Date.parse(`${match[1]}Z`);
  return Number.isFinite(whole) ? BigInt(whole) * 1000n + BigInt((match[2] || "").padEnd(6, "0")) : null;
}
export const intervalSchema = z.strictObject({ start: utc, end: utc }).refine((x) => {
  const start = sourceMicros(x.start), end = sourceMicros(x.end);
  return start !== null && end !== null && start < end;
}, "Empty or reversed interval");
const rational = z.strictObject({ numerator: integer, denominator: integer.min(1) });
export const coverageSchema = z.strictObject({
  fraction: number.min(0).max(1), support_seconds: nonnegative, expected_seconds: nonnegative,
  algorithm_revision: id, gaps: z.array(intervalSchema).max(1000),
});
export const canonicalUnits = {
  temperature: "degC", humidity: "%", wind_lull: "m/s", wind_avg: "m/s", wind_speed: "m/s", wind_gust: "m/s", wind_direction: "deg",
  precipitation: "mm", rain_last_hour: "mm", rain_today: "mm", station_pressure: "hPa", illuminance: "lx", uv: "index", irradiance: "W/m2", lightning_count: "1", lightning_distance: "km",
} as const;
const metricID = z.enum(Object.keys(canonicalUnits) as [keyof typeof canonicalUnits, ...(keyof typeof canonicalUnits)[]]);
export function isDirectMetric(metric: string): boolean { return metric !== "rain_last_hour" && metric !== "rain_today"; }
export const metricSchema = z.strictObject({
  metric: metricID, value: number.nullable(), unit: z.string().min(1).max(32),
  observed_at: utc.nullable(), received_at: utc.nullable(), expected_interval_seconds: number.positive().nullable(),
  valid_until: utc.nullable(), quality: z.enum(["valid", "suspect", "missing", "invalid"]),
  freshness: z.enum(["fresh", "stale", "unavailable"]), reason,
}).superRefine((metric, context) => {
  if (metric.unit !== canonicalUnits[metric.metric] || (metric.value !== null && (!metric.observed_at || !metric.received_at || ["invalid", "missing"].includes(metric.quality))) || (metric.freshness !== "fresh" && !metric.reason)) {
    context.addIssue({ code: "custom", message: "Measurements require canonical units, source timing and explicit quality" });
  }
  if (metric.valid_until && (!metric.observed_at || sourceMicros(metric.valid_until)! <= sourceMicros(metric.observed_at)!)) context.addIssue({ code: "custom", message: "Expiry must follow source time" });
  if (metric.metric === "lightning_count" && metric.value !== null && (!Number.isSafeInteger(metric.value) || metric.value < 0)) context.addIssue({ code: "custom", message: "Counts require nonnegative integers" });
  if (metric.value === null && (!metric.reason || metric.freshness !== "unavailable")) {
    context.addIssue({ code: "custom", message: "Missing metrics require a reason and unavailable state" });
  }
  if (metric.freshness === "fresh" && (metric.value === null || !metric.observed_at || !metric.received_at || !metric.valid_until || metric.quality !== "valid")) {
    context.addIssue({ code: "custom", message: "Fresh metrics require valid source timing and quality" });
  }
});
export const currentSchema = z.strictObject({
  schema_version: z.literal(1), generated_at: utc, station_alias: z.string().min(1).max(128),
  data_state: z.enum(["live", "cached", "unavailable"]), metrics: z.array(metricSchema).max(128),
}).superRefine((data, context) => {
  if (new Set(data.metrics.map((metric) => metric.metric)).size !== data.metrics.length || (!data.metrics.length && data.data_state !== "unavailable")) context.addIssue({ code: "custom", message: "Invalid current metric identities or state" });
  for (const metric of data.metrics) if (metric.freshness === "fresh" && (sourceMicros(metric.observed_at!)! > sourceMicros(data.generated_at)! || sourceMicros(metric.received_at!)! > sourceMicros(data.generated_at)! || sourceMicros(data.generated_at)! >= sourceMicros(metric.valid_until!)!)) context.addIssue({ code: "custom", message: "Response time cannot freshen source evidence" });
});
const historyPoint = z.strictObject({
  observed_at: utc, received_at: utc, metric: metricID, value: number.nullable(), unit: z.string().min(1).max(32),
  quality: z.enum(["valid", "suspect", "missing", "invalid"]), evidence_id: id,
  reason, support_interval: intervalSchema.nullable(),
}).refine((point) => point.unit === canonicalUnits[point.metric] && (point.value !== null || !!point.reason) && !(point.value !== null && ["invalid", "missing"].includes(point.quality)) && (point.metric !== "lightning_count" || point.value === null || (Number.isSafeInteger(point.value) && point.value >= 0)), "Unqualified observation");
export const historySchema = z.strictObject({
  generated_at: utc, interval: intervalSchema, resolution_seconds: z.literal(0), aggregation: z.literal("observed"),
  points: z.array(historyPoint).max(5000), gaps: z.array(intervalSchema).max(1000),
  next_cursor: z.string().min(1).max(2048).nullable(),
}).refine((page) => new Set(page.points.map((point) => point.evidence_id)).size === page.points.length && page.points.every((point) => sourceMicros(point.observed_at)! >= sourceMicros(page.interval.start)! && sourceMicros(point.observed_at)! < sourceMicros(page.interval.end)!) && page.gaps.every((gap) => sourceMicros(gap.start)! >= sourceMicros(page.interval.start)! && sourceMicros(gap.end)! <= sourceMicros(page.interval.end)!), "Observation page exceeds source bounds");
const asset = z.strictObject({
  asset_id: id, class: z.enum(["video", "poster", "public_still", "keogram", "camera_preview", "metadata"]),
  url: z.string().regex(/^\/media\/[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/),
  mime: z.enum(["video/mp4", "image/jpeg", "image/png", "image/webp", "application/json"]),
  bytes: integer.min(1), sha256: z.string().regex(/^[0-9a-f]{64}$/), width: integer.min(1).nullable(), height: integer.min(1).nullable(),
}).refine((item) => item.class === "metadata" || (item.width !== null && item.height !== null), "Public imagery requires dimensions");
const rendition = z.strictObject({
  role: z.enum(["native", "web", "mobile"]), asset, codec: text, profile: text, level: text,
  fps: rational, frame_count: integer, duration: rational, track_timescale: z.literal(60000), movie_timescale: z.literal(60000),
});
const precision = z.enum(["frame", "interval", "clip", "segment"]);
const seek = z.strictObject({ start_seconds: nonnegative, end_seconds: nonnegative, precision, source_interval: intervalSchema });
const claim = z.strictObject({
  claim_id: id, text: z.string().min(1).max(4096), origin: z.enum(["visual", "measured", "human", "synthesis"]),
  source_interval: intervalSchema, precision, evidence_ids: z.array(id).max(100), seek: seek.nullable(),
  basis: z.enum(["visual", "instrument", "inference"]),
});
const aggregate = z.strictObject({
  metric: text, statistic: text, value: number.nullable(), unit: text, interval: intervalSchema,
  coverage: coverageSchema, evidence_ids: z.array(id).max(1000), evidence_revision: id, reason, algorithm_revision: id,
});
export const videoSchema = z.strictObject({
  schema_version: z.literal(1), video_id: id, camera: cameraSchema, cycle_date: date, segment: segmentSchema,
  media_revision: integer.min(1), metadata_revision: integer.min(1),
  state: z.enum(["capturing", "queued", "processing", "published", "partial", "unavailable", "expired", "withdrawn"]),
  reason, updated_at: utc, source_interval: intervalSchema, local_start_label: text, local_end_label: text,
  timezone: z.literal("America/Denver"), twilight_model: id, allocated_frames: integer.min(1), fps: rational, duration: rational,
  time_map: z.array(z.strictObject({ frame_start: integer, frame_end: integer.min(1), source_interval: intervalSchema,
    uncertainty_seconds: nonnegative, coverage: coverageSchema, mode: z.enum(["observed_exposure", "motion_exposure", "gap"]) })).max(10000),
  expected_slots: integer, accepted_slots: integer,
  cadence_epochs: z.array(z.strictObject({ interval: intervalSchema, interval_seconds: number.positive() })).max(100),
  source_time_basis: text, coverage: coverageSchema, caveats: z.array(text).max(64), renditions: z.array(rendition).max(3),
  poster: z.strictObject({ asset, source_interval: intervalSchema, output_pts: rational,
    source_kind: z.enum(["observed_capture", "decoded_output_frame"]), recipe_revision: id }).nullable(),
  native_download_url: z.string().regex(/^\/downloads\/[A-Za-z0-9][A-Za-z0-9_-]{0,127}\/native$/).nullable(),
  keogram_id: id.nullable(), keogram_state: z.enum(["pending", "published", "partial", "unavailable", "expired"]),
  calendar_day_ids: z.array(id).max(4), public_still_ids: z.array(id).max(100), event_ids: z.array(id).max(1000), weather: z.array(aggregate).max(128),
  analysis: z.strictObject({ state: z.enum(["pending", "running", "complete", "partial", "unavailable", "invalidated"]),
    mode: z.enum(["native_video", "sampled_frames", "none"]), claims: z.array(claim).max(1000), limitations: z.array(text).max(100),
    prompt_revision: id.nullable(), schema_revision: id, capability_revision: id.nullable(), evidence_revision: id.nullable(),
    coverage: coverageSchema, execution_state: z.enum(["pending", "running", "complete", "degraded", "failed", "unsupported"]), reason }),
  editorial: z.strictObject({ effective_revision: integer.min(1), human_edited: z.boolean(), updated_at: utc,
    public_tags: z.array(text).max(100), description: z.string().min(1).max(8000).nullable() }),
  search: z.strictObject({ state: z.enum(["pending", "indexed", "degraded", "withdrawn"]), profile_id: id.nullable(), generation_id: id.nullable(), metadata_revision: integer.min(1) }),
  input_manifest_hash: z.string().regex(/^[0-9a-f]{64}$/), recipe_revision: id, build_identity: id, publication_generation: integer.min(1),
}).superRefine((video, context) => {
  const roles = new Set(video.renditions.map((item) => item.role));
  if (video.state === "published" && (roles.size !== 3 || !video.poster || !video.native_download_url)) context.addIssue({ code: "custom", message: "Published products require three renditions, poster and native download" });
  if (roles.size !== video.renditions.length) context.addIssue({ code: "custom", message: "Duplicate rendition roles" });
  if (!["published", "partial"].includes(video.state) && (video.renditions.length || video.poster || video.native_download_url)) {
    context.addIssue({ code: "custom", message: "Unavailable media cannot expose assets" });
  }
  if (video.renditions.some((item) => item.asset.class !== "video") || (video.poster && video.poster.asset.class !== "poster")) {
    context.addIssue({ code: "custom", message: "Incorrect public asset class" });
  }
});
export const catalogSchema = z.strictObject({ generated_at: utc, revision: id, items: z.array(videoSchema).max(100), next_cursor: z.string().max(4096).nullable() });
const signedInteger = z.number().int().refine(Number.isSafeInteger);
const conversionSchema = z.strictObject({ unit: z.string().min(1).max(32), scale_numerator: signedInteger, scale_denominator: signedInteger.positive(), offset_numerator: signedInteger, offset_denominator: signedInteger.positive() });
export const metricDefinitionSchema = z.strictObject({
  metric: metricID, label: z.string().min(1).max(128), canonical_unit: z.string().min(1).max(32),
  display_units: z.array(z.string().min(1).max(32)).min(1).max(16), conversions: z.array(conversionSchema).min(1).max(16),
  statistics: z.array(z.string().min(1).max(64)).min(1).max(16), expected_interval_seconds: number.positive().nullable(), public: z.literal(true), revision: id,
}).refine((row) => {
  const identity = row.conversions.find((item) => item.unit === row.canonical_unit);
  return row.canonical_unit === canonicalUnits[row.metric] && row.display_units.join("|") === row.conversions.map((item) => item.unit).join("|") && new Set(row.display_units).size === row.display_units.length && new Set(row.statistics).size === row.statistics.length && !!identity && identity.scale_numerator === identity.scale_denominator && identity.offset_numerator === 0;
}, "Invalid explicit conversion recipe");
export const metricRegistrySchema = z.strictObject({ revision: id, metrics: z.array(metricDefinitionSchema).max(256) }).refine((registry) => new Set(registry.metrics.map((row) => row.metric)).size === registry.metrics.length, "Duplicate registry identities");
export type MetricDefinition = z.infer<typeof metricDefinitionSchema>;
export type MetricRegistry = z.infer<typeof metricRegistrySchema>;
export type Current = z.infer<typeof currentSchema>;
export type Metric = z.infer<typeof metricSchema>;
export type History = z.infer<typeof historySchema>;
export type Video = z.infer<typeof videoSchema>;
export type Catalog = z.infer<typeof catalogSchema>;
export type Camera = z.infer<typeof cameraSchema>;
export type Units = "us" | "metric";
