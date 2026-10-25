// SPDX-License-Identifier: MIT
/**
 * Search (#27): the shareable /search/ URL, the typed POST /api/v1/search request it stands for
 * (types generated from #25), the lenient response parser, and the evidence a result may claim.
 * The URL carries local calendar days (America/Denver, last day inclusive), explicit units on
 * every measured filter, and the display units. Each URL maps to exactly one request; an invalid
 * parameter is reported, never dropped, and nothing is truncated or widened (D12).
 */
import { z } from "zod";
import type { components } from "../generated/weather-api";
import { dayCount, localMidnight } from "./almanac";
import { cameraSchema, segmentSchema, type Camera, type Units } from "./contracts";
import { CAMERA_LABELS, dateShift, localDate, metricValue } from "./format";
import { instant, iso, MAX_FRAMES, within, type Span } from "./time-map";
import type { MetricDefinition } from "./weather-api";
z.config({ jitless: true });

type Schemas = components["schemas"];
export type SearchRequest = Schemas["SearchRequest"];
export type SearchResults = Schemas["SearchResults"];
export type SearchHit = Schemas["SearchHit"];
export type MatchedMeasurement = Schemas["MatchedMeasurement"];
export type Predicate = Schemas["Predicate"];
type Segment = SearchRequest["segments"][number];

export const MAX_QUERY = 512;
export const MAX_PREDICATES = 8;
export const MAX_TOP_K = 20;
export const DEFAULT_TOP_K = 10;
export const DEFAULT_COVERAGE = 0.95;
const PREDICATE_WINDOW_MS = 31 * 86_400_000;
const EARLIEST = Date.parse("2000-01-01T00:00:00Z"), LATEST = Date.parse("2100-01-01T00:00:00Z");

/** Whole-recording statistics (v1 scope): each applies to the entire video, never to a clip time. */
export const MEASURES = {
  "precipitation.sum": { metric: "precipitation", statistic: "sum", label: "Rain total", units: ["in", "mm"], help: "Observed accumulation over the whole recording, never scaled up for missing time." },
  "wind_gust.max": { metric: "wind_gust", statistic: "max", label: "Maximum gust", units: ["mph", "km/h", "m/s"], help: "The highest gust reported over the whole recording, not the time of the cited window." },
  "temperature.min": { metric: "temperature", statistic: "min", label: "Lowest temperature", units: ["degF", "degC"], help: "The lowest air temperature over the whole recording." },
  "temperature.max": { metric: "temperature", statistic: "max", label: "Highest temperature", units: ["degF", "degC"], help: "The highest air temperature over the whole recording." },
  "temperature.mean": { metric: "temperature", statistic: "mean", label: "Mean temperature", units: ["degF", "degC"], help: "The duration-weighted mean air temperature over the whole recording." },
} as const satisfies Record<string, { metric: Predicate["metric"]; statistic: Predicate["statistic"]; label: string; units: readonly string[]; help: string }>;
export type Measure = keyof typeof MEASURES;
export const OPERATORS = { lt: "<", le: "<=", eq: "=", ge: ">=", gt: ">" } as const satisfies Record<string, Predicate["op"]>;
export const OPERATOR_LABELS = { lt: "less than", le: "at most", eq: "equal to", ge: "at least", gt: "more than" } as const;
type Op = keyof typeof OPERATORS;
export const UNIT_LABELS: Record<string, string> = { mm: "mm", in: "in", "m/s": "m/s", "km/h": "km/h", mph: "mph", degC: "°C", degF: "°F" };
/** The filter unit a new row starts with under the site US/metric toggle. */
export function defaultUnit(measure: Measure, units: Units): string {
  const choices = MEASURES[measure].units;
  return units === "us" ? choices[0] : choices[choices.length - 1];
}

/** The form's view of the URL: raw strings, so an invalid value stays visible for correction. */
export type DraftFilter = { measure: string; op: string; value: string; unit: string; coverage: string };
export type Draft = { q: string; from: string; to: string; cameras: string[]; segments: string[]; filters: DraftFilter[]; topK: string; units: string };
/** A validated search: everything the URL and the request hold. */
export type SearchQuery = { query: string; from: string; to: string; cameras: Camera[]; segments: Segment[]; predicates: Predicate[]; top_k: number; units: Units };

const SEARCH_KEYS = ["q", "from", "to", "camera", "segment", "p", "top_k"] as const;
const DATE = /^\d{4}-\d\d-\d\d$/;
const DECIMAL = /^-?\d{1,7}(?:\.\d{1,6})?$/;
const PERCENT = /^\d{1,3}(?:\.\d{1,4})?$/;
const FRACTION = /^\d(?:\.\d{1,6})?$/;
// Python's Cc category and lone surrogates, exactly what #25 rejects.
// eslint-disable-next-line no-control-regex -- matching control characters is the point
const CONTROL = /[\u0000-\u001f\u007f-\u009f]/;
const LONE_SURROGATE = /[\ud800-\udbff](?![\udc00-\udfff])|(?<![\ud800-\udbff])[\udc00-\udfff]/;

const toPercent = (fraction: string) => FRACTION.test(fraction) ? String(Math.round(Number(fraction) * 1e6) / 1e4) : fraction;
const toFraction = (percent: string) => PERCENT.test(percent) ? String(Math.round(Number(percent) * 1e4) / 1e6) : percent;

export function emptyFilter(units: Units): DraftFilter {
  return { measure: "precipitation.sum", op: "ge", value: "", unit: defaultUnit("precipitation.sum", units), coverage: toPercent(String(DEFAULT_COVERAGE)) };
}
/** Read the URL into a draft. `submitted` is false for a bare /search/ (no request, no prefetch). */
export function draftFromParams(params: URLSearchParams, today: string): { draft: Draft; submitted: boolean } {
  const submitted = SEARCH_KEYS.some((key) => params.has(key));
  const filters = params.getAll("p").map((value): DraftFilter => {
    const [measure = "", op = "", amount = "", unit = "", coverage, ...extra] = value.split(":");
    if (extra.length) return { measure: value, op, value: amount, unit, coverage: "" };
    return { measure, op, value: amount, unit, coverage: coverage === undefined ? toPercent(String(DEFAULT_COVERAGE)) : toPercent(coverage) };
  });
  return {
    submitted,
    draft: {
      q: params.get("q") ?? "", from: params.get("from") ?? (submitted ? "" : dateShift(today, -6)), to: params.get("to") ?? (submitted ? "" : today),
      cameras: params.getAll("camera"), segments: params.getAll("segment"), filters, topK: params.get("top_k") ?? String(DEFAULT_TOP_K), units: params.get("units") ?? "us",
    },
  };
}
/** A draft as URL parameters, exactly as entered (used when it does not validate). */
export function paramsFromDraft(draft: Draft): URLSearchParams {
  const params = new URLSearchParams();
  if (draft.q) params.set("q", draft.q);
  params.set("from", draft.from); params.set("to", draft.to);
  for (const camera of draft.cameras) params.append("camera", camera);
  for (const segment of draft.segments) params.append("segment", segment);
  for (const filter of draft.filters) params.append("p", [filter.measure, filter.op, filter.value, filter.unit, toFraction(filter.coverage)].join(":"));
  if (draft.topK !== String(DEFAULT_TOP_K)) params.set("top_k", draft.topK);
  params.set("units", draft.units);
  return params;
}
function calendarDay(value: string): boolean {
  const parsed = new Date(`${value}T12:00:00Z`);
  return DATE.test(value) && Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
}
const measureOf = (metric: string, statistic: string) => (Object.keys(MEASURES) as Measure[]).find((key) => MEASURES[key].metric === metric && MEASURES[key].statistic === statistic);
const opOf = (op: string) => (Object.keys(OPERATORS) as Op[]).find((key) => OPERATORS[key] === op);

/** Every problem with a draft, in form order; a search runs only when there are none. */
export function validate(draft: Draft): { query: SearchQuery | null; errors: string[] } {
  const errors: string[] = [];
  const length = [...draft.q].length;
  if (length > MAX_QUERY) errors.push(`Search text is ${length} characters; the limit is ${MAX_QUERY}.`);
  if (CONTROL.test(draft.q) || LONE_SURROGATE.test(draft.q)) errors.push("Search text contains control or malformed characters.");
  const fromOk = calendarDay(draft.from), toOk = calendarDay(draft.to);
  if (!fromOk) errors.push(draft.from ? `First day “${draft.from}” is not a calendar date.` : "Choose a first day.");
  if (!toOk) errors.push(draft.to ? `Last day “${draft.to}” is not a calendar date.` : "Choose a last day.");
  let start = "", end = "";
  if (fromOk && toOk) {
    if (dayCount(draft.from, draft.to) < 1) errors.push("The last day is before the first day.");
    else {
      start = localMidnight(draft.from); end = localMidnight(dateShift(draft.to, 1));
      if (Date.parse(start) < EARLIEST || Date.parse(end) > LATEST) errors.push("Dates must fall between 2000 and 2099.");
      else if (draft.filters.length && Date.parse(end) - Date.parse(start) > PREDICATE_WINDOW_MS) errors.push("Measured filters cover at most 31 days per search; narrow the dates or split the search.");
    }
  }
  const cameras = draft.cameras.flatMap((value) => { const parsed = cameraSchema.safeParse(value); if (!parsed.success) errors.push(`Unknown camera “${value}”.`); return parsed.success ? [parsed.data] : []; });
  const segments = draft.segments.flatMap((value) => { const parsed = segmentSchema.safeParse(value); if (!parsed.success) errors.push(`Unknown segment “${value}”.`); return parsed.success ? [parsed.data] : []; });
  if (new Set(cameras).size !== cameras.length || new Set(segments).size !== segments.length) errors.push("A camera or segment is listed twice.");
  if (draft.filters.length > MAX_PREDICATES) errors.push(`${draft.filters.length} measured filters; the limit is ${MAX_PREDICATES}.`);
  const predicates = draft.filters.flatMap((filter, index): Predicate[] => {
    const label = `Measured filter ${index + 1}`;
    const measure = Object.hasOwn(MEASURES, filter.measure) ? MEASURES[filter.measure as Measure] : null;
    const problems: string[] = [];
    if (!measure) problems.push(`${label}: unknown measurement “${filter.measure}”.`);
    if (!Object.hasOwn(OPERATORS, filter.op)) problems.push(`${label}: unknown comparison “${filter.op}”.`);
    if (!DECIMAL.test(filter.value)) problems.push(`${label}: enter a number (up to six decimal places).`);
    if (measure && !(measure.units as readonly string[]).includes(filter.unit)) problems.push(`${label}: “${filter.unit}” is not a unit of ${measure.label.toLowerCase()}.`);
    const coverage = PERCENT.test(filter.coverage) ? Number(filter.coverage) : NaN;
    if (!(coverage > 0 && coverage <= 100)) problems.push(`${label}: minimum coverage must be above 0% and at most 100%.`);
    errors.push(...problems);
    if (problems.length || !measure) return [];
    return [{ metric: measure.metric, statistic: measure.statistic, op: OPERATORS[filter.op as Op], value: Number(filter.value), unit: filter.unit, min_coverage: Math.round(coverage * 1e4) / 1e6 }];
  });
  const topK = /^\d{1,2}$/.test(draft.topK) ? Number(draft.topK) : NaN;
  if (!(topK >= 1 && topK <= MAX_TOP_K)) errors.push(`Results per search must be a whole number from 1 to ${MAX_TOP_K}.`);
  if (draft.units !== "us" && draft.units !== "metric") errors.push(`Unknown display units “${draft.units}”.`);
  if (errors.length) return { query: null, errors };
  return { query: { query: draft.q, from: draft.from, to: draft.to, cameras, segments, predicates, top_k: topK, units: draft.units as Units }, errors };
}
/** The canonical URL of a validated search; display units are always explicit. */
export function paramsFromQuery(query: SearchQuery): URLSearchParams {
  const params = new URLSearchParams();
  if (query.query) params.set("q", query.query);
  params.set("from", query.from); params.set("to", query.to);
  for (const camera of query.cameras) params.append("camera", camera);
  for (const segment of query.segments) params.append("segment", segment);
  for (const predicate of query.predicates) params.append("p", [`${predicate.metric}.${predicate.statistic}`, opOf(predicate.op), String(predicate.value), predicate.unit, String(predicate.min_coverage)].join(":"));
  if (query.top_k !== DEFAULT_TOP_K) params.set("top_k", String(query.top_k));
  params.set("units", query.units);
  return params;
}
/** Local days → the half-open UTC interval [00:00 first day, 00:00 after the last day). */
export function searchRequest(query: SearchQuery): SearchRequest {
  return {
    start: localMidnight(query.from), end: localMidnight(dateShift(query.to, 1)), query: query.query,
    cameras: query.cameras, segments: query.segments, predicates: query.predicates, top_k: query.top_k,
  };
}
/** The inverse of searchRequest, for a request whose bounds are America/Denver midnights. */
export function queryFromRequest(request: SearchRequest, units: Units): SearchQuery | null {
  const from = localDate(request.start), to = dateShift(localDate(request.end), -1);
  if (localMidnight(from) !== request.start || localMidnight(dateShift(to, 1)) !== request.end) return null;
  const draft: Draft = {
    q: request.query, from, to, cameras: [...request.cameras], segments: [...request.segments], topK: String(request.top_k), units,
    filters: request.predicates.map((predicate) => ({ measure: `${predicate.metric}.${predicate.statistic}`, op: opOf(predicate.op) || predicate.op, value: String(predicate.value), unit: predicate.unit, coverage: toPercent(String(predicate.min_coverage)) })),
  };
  return validate(draft).query;
}

// Lenient response parsing (P12): unknown fields are dropped; an unknown mode, reason or
// precision degrades to the most cautious known value; a malformed hit is dropped, never the page.
const text = z.string();
const interval = z.object({ start: text, end: text });
const coverage = z.object({ fraction: z.number(), support_seconds: z.number(), expected_seconds: z.number() });
const measurement = z.object({
  metric: text, statistic: text, value: z.number(), unit: text, interval, coverage,
  evidence_count: z.number(), evidence_hash: text.nullable().default(null),
});
const hit = z.object({
  rank: z.number(), video_id: text, document_id: text, camera_id: text, segment: z.enum(["daytime", "overnight"]),
  video_interval: interval, source_interval: interval,
  evidence_precision: z.enum(["frame", "interval", "clip", "segment"]).catch("segment"),
  analysis_mode: text, snippet: text, media_revision: z.number(), poster_asset_id: text.nullable().default(null),
  seek: z.object({ media_seconds: z.number(), source_at: text }).nullable().catch(null).default(null),
  matched_measurements: z.array(measurement.nullable().catch(null)).transform((items) => items.filter((item) => item !== null)),
});
const results = z.object({
  generated_at: text, mode: z.enum(["hybrid_exact", "lexical", "numeric"]).catch("lexical"),
  degraded_reason: z.enum(["embedding_unavailable", "embedding_busy", "lexical_no_terms"]).nullable().catch(null).default(null),
  profile_id: text.nullable().default(null), count: z.number(),
  hits: z.array(hit.nullable().catch(null)).transform((items) => items.filter((item) => item !== null)),
});
const assignable: (value: z.infer<typeof results>) => SearchResults = (value) => value;
void assignable;
export function parseSearchResults(value: unknown): SearchResults | null {
  const parsed = results.safeParse(value);
  return parsed.success ? parsed.data : null;
}

export type Outcome =
  | { kind: "results"; data: SearchResults }
  | { kind: "invalid"; code: "invalid_request" | "range_too_large" }
  | { kind: "busy"; retryAfter: number | null }
  | { kind: "unavailable" };
/** One response, classified. 429 is busy (with its Retry-After), anything else not 2xx/400 is unavailable. */
export async function readOutcome(response: Response): Promise<Outcome> {
  if (response.status === 429) {
    const seconds = Number(response.headers.get("Retry-After"));
    return { kind: "busy", retryAfter: Number.isInteger(seconds) && seconds > 0 && seconds <= 3600 ? seconds : null };
  }
  if (response.status === 400) {
    const body: unknown = await response.json().catch(() => null);
    const code = z.object({ code: text }).safeParse(body);
    return { kind: "invalid", code: code.success && code.data.code === "range_too_large" ? "range_too_large" : "invalid_request" };
  }
  if (response.status !== 200) return { kind: "unavailable" };
  const data = parseSearchResults(await response.json().catch(() => null));
  return data ? { kind: "results", data } : { kind: "unavailable" };
}

const span = (value: { start: string; end: string }): Span | null => {
  const start = instant(value.start), end = instant(value.end);
  return start !== null && end !== null && start < end ? { start, end } : null;
};
const MAX_MEDIA_SECONDS = MAX_FRAMES / 60;
/**
 * The video-page link for a hit's seek, or null ("location unavailable"). The seek's source instant
 * must lie in the cited interval, which must lie in the recording's interval (half-open, exact
 * microseconds), and its media offset must be inside a segment's player timeline. The video page
 * then resolves `t` through the sidecar time map (#40).
 */
export function seekLink(item: SearchHit): string | null {
  if (!item.seek) return null;
  const camera = cameraSchema.safeParse(item.camera_id);
  const video = span(item.video_interval), cited = span(item.source_interval), at = instant(item.seek.source_at);
  if (!camera.success || !video || !cited || at === null) return null;
  if (cited.start < video.start || cited.end > video.end || !within(cited, at) || !within(video, at)) return null;
  const seconds = item.seek.media_seconds;
  if (!Number.isFinite(seconds) || seconds < 0 || seconds >= MAX_MEDIA_SECONDS) return null;
  return `/timelapses/${camera.data}/${cycleDate(item)}/${item.segment}/?t=${encodeURIComponent(iso(at))}`;
}
/** The solar-cycle date (dawn to dawn) a recording belongs to: the local date its segment begins. */
export function cycleDate(item: Pick<SearchHit, "video_interval">): string { return localDate(item.video_interval.start); }
export function cameraLabel(id: string): string { return CAMERA_LABELS[id as Camera] ?? id; }

export type VideoResult = { video: SearchHit; hits: SearchHit[]; measurements: MatchedMeasurement[] };
/** One card per video in best-rank order, listing every cited window; measurements once each. */
export function groupHits(hits: readonly SearchHit[]): VideoResult[] {
  const groups = new Map<string, VideoResult>();
  for (const item of [...hits].sort((a, b) => a.rank - b.rank)) {
    const group = groups.get(item.video_id);
    if (!group) { groups.set(item.video_id, { video: item, hits: [item], measurements: [...item.matched_measurements] }); continue; }
    group.hits.push(item);
    for (const measured of item.matched_measurements) {
      if (!group.measurements.some((known) => known.metric === measured.metric && known.statistic === measured.statistic && known.interval.start === measured.interval.start && known.interval.end === measured.interval.end)) group.measurements.push(measured);
    }
  }
  return [...groups.values()];
}

/** A matched value in display units. Unknown or under-covered rain is "Unknown", never "0 in". */
export function measurementValue(item: MatchedMeasurement, units: Units, definition?: MetricDefinition): string {
  const value = Number.isFinite(item.value) ? item.value : null;
  const complete = item.coverage.fraction >= DEFAULT_COVERAGE;
  if (value === null || (item.metric === "precipitation" && !complete && value === 0)) return "Unknown";
  const shown = metricValue(value, item.unit, units, definition);
  if (complete) return shown;
  // Partial rain is a lower bound; other partial statistics are labelled, not bounded.
  return item.metric === "precipitation" ? `At least ${shown}` : `${shown} (partial coverage)`;
}
export function measureLabel(item: Pick<MatchedMeasurement, "metric" | "statistic">): string {
  const measure = measureOf(item.metric, item.statistic);
  return measure ? MEASURES[measure].label : `${item.metric.replaceAll("_", " ")} ${item.statistic}`;
}
