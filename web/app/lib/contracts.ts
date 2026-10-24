// SPDX-License-Identifier: MIT
/** Public boundary schemas are hand-maintained against api-contract.md until WX-13 generates types. */
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
export const metricSchema = z.strictObject({
  metric: z.string().min(1).max(64), value: number.nullable(), unit: z.string().min(1).max(32),
  observed_at: utc.nullable(), received_at: utc.nullable(), expected_interval_seconds: number.positive(),
  valid_until: utc.nullable(), quality: z.enum(["valid", "suspect", "missing", "invalid"]),
  freshness: z.enum(["fresh", "stale", "unavailable"]), reason,
}).superRefine((metric, context) => {
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
}).refine((data) => new Set(data.metrics.map((metric) => metric.metric)).size === data.metrics.length, "Duplicate metric identities");
const historyPoint = z.strictObject({
  observed_at: utc, received_at: utc, metric: text, value: number.nullable(), unit: text,
  quality: z.enum(["valid", "suspect", "missing", "invalid"]), evidence_id: id,
  reason, support_interval: intervalSchema.nullable(),
});
export const historySchema = z.strictObject({
  generated_at: utc, interval: intervalSchema, resolution_seconds: integer.min(1), aggregation: text,
  points: z.array(historyPoint).max(5000), gaps: z.array(intervalSchema).max(1000),
  next_cursor: z.string().max(4096).nullable(),
});
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
export type Current = z.infer<typeof currentSchema>;
export type Metric = z.infer<typeof metricSchema>;
export type History = z.infer<typeof historySchema>;
export type Video = z.infer<typeof videoSchema>;
export type Catalog = z.infer<typeof catalogSchema>;
export type Camera = z.infer<typeof cameraSchema>;
export type Units = "us" | "metric";
