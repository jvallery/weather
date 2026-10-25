// SPDX-License-Identifier: MIT
import { expect, test } from "vitest";
import {
  draftFromParams, groupHits, measurementValue, paramsFromDraft, paramsFromQuery, parseSearchResults, queryFromRequest, readOutcome, searchRequest, seekLink, validate,
  type SearchHit,
} from "./search";
import { parseRegistry } from "./weather-api";
import fixture from "../fixtures/search.json";
import registryFixture from "../fixtures/metric-registry.json";

const TODAY = "2026-11-01";
const parse = (search: string) => { const { draft, submitted } = draftFromParams(new URLSearchParams(search), TODAY); return { submitted, draft, ...validate(draft) }; };
const results = parseSearchResults(fixture)!;
const hit = (rank: number) => results.hits.find((item) => item.rank === rank)!;
const registry = parseRegistry(registryFixture)!;
const definition = (metric: string) => registry.metrics.find((item) => item.metric === metric);

test("every filter round-trips URL → typed request → URL, in each unit variant", () => {
  const canonical = new URLSearchParams([
    ["q", "rain over the ridge"], ["from", "2026-10-25"], ["to", "2026-10-31"], ["camera", "skycam_east"], ["camera", "frontyard_1"], ["segment", "daytime"],
    ["p", "precipitation.sum:ge:0.5:in:0.95"], ["p", "precipitation.sum:lt:12.7:mm:0.8"],
    ["p", "wind_gust.max:gt:20:mph:0.95"], ["p", "wind_gust.max:le:40:km/h:0.9"], ["p", "wind_gust.max:eq:0:m/s:1"],
    ["p", "temperature.min:lt:32:degF:0.95"], ["p", "temperature.max:ge:-5.25:degC:0.95"], ["p", "temperature.mean:ge:10:degC:0.5"],
    ["top_k", "20"], ["units", "metric"],
  ]).toString();
  const { query, errors } = parse(canonical);
  expect(errors).toEqual([]);
  const request = searchRequest(query!);
  expect(request).toEqual({
    // Local calendar days [2026-10-25, 2026-10-31] → half-open UTC, across no DST change.
    start: "2026-10-25T06:00:00Z", end: "2026-11-01T06:00:00Z", query: "rain over the ridge",
    cameras: ["skycam_east", "frontyard_1"], segments: ["daytime"], top_k: 20,
    predicates: [
      { metric: "precipitation", statistic: "sum", op: ">=", value: 0.5, unit: "in", min_coverage: 0.95 },
      { metric: "precipitation", statistic: "sum", op: "<", value: 12.7, unit: "mm", min_coverage: 0.8 },
      { metric: "wind_gust", statistic: "max", op: ">", value: 20, unit: "mph", min_coverage: 0.95 },
      { metric: "wind_gust", statistic: "max", op: "<=", value: 40, unit: "km/h", min_coverage: 0.9 },
      { metric: "wind_gust", statistic: "max", op: "=", value: 0, unit: "m/s", min_coverage: 1 },
      { metric: "temperature", statistic: "min", op: "<", value: 32, unit: "degF", min_coverage: 0.95 },
      { metric: "temperature", statistic: "max", op: ">=", value: -5.25, unit: "degC", min_coverage: 0.95 },
      { metric: "temperature", statistic: "mean", op: ">=", value: 10, unit: "degC", min_coverage: 0.5 },
    ],
  });
  expect(paramsFromQuery(queryFromRequest(request, "metric")!).toString()).toBe(canonical);
  // Display units are always explicit; the default result count is implied.
  const minimal = parse("from=2026-03-08&to=2026-03-08");
  expect(paramsFromQuery(minimal.query!).toString()).toBe("from=2026-03-08&to=2026-03-08&units=us");
  // The spring-forward day is 23 hours long, still exactly [00:00, 00:00 next day).
  expect([searchRequest(minimal.query!).start, searchRequest(minimal.query!).end]).toEqual(["2026-03-08T07:00:00Z", "2026-03-09T06:00:00Z"]);
  expect(searchRequest(minimal.query!)).toMatchObject({ query: "", cameras: [], segments: [], predicates: [], top_k: 10 });
});

test("a bare page sends nothing; the form starts on the last seven local days in the site units", () => {
  const { submitted, draft } = parse("units=metric");
  expect(submitted).toBe(false);
  expect([draft.from, draft.to, draft.units]).toEqual(["2026-10-26", "2026-11-01", "metric"]);
});

test("D12 ceilings and invalid parameters are visible errors, never dropped or truncated", () => {
  const base = "from=2026-10-25&to=2026-10-31&units=us";
  const errors = (extra: string) => parse(`${base}&${extra}`).errors;
  expect(errors(`q=${"a".repeat(512)}`)).toEqual([]);
  // 512 Unicode scalars (1,024 UTF-16 code units) is still within the limit.
  expect(errors(`q=${encodeURIComponent("🌧".repeat(512))}`)).toEqual([]);
  expect(errors(`q=${"a".repeat(513)}`)).toEqual(["Search text is 513 characters; the limit is 512."]);
  expect(errors("q=rain%07")).toEqual(["Search text contains control or malformed characters."]);
  expect(errors("top_k=21")).toHaveLength(1);
  expect(errors("top_k=0")).toHaveLength(1);
  expect(errors("top_k=20")).toEqual([]);
  expect(errors(Array.from({ length: 8 }, () => "p=wind_gust.max:gt:10:mph").join("&"))).toEqual([]);
  expect(errors(Array.from({ length: 9 }, () => "p=wind_gust.max:gt:10:mph").join("&"))).toEqual(["9 measured filters; the limit is 8."]);
  expect(errors("p=precipitation.sum:ge:1:mph:0.95")).toEqual(["Measured filter 1: “mph” is not a unit of rain total."]);
  expect(errors("p=precipitation.median:ge:1:in:0.95")).toEqual(["Measured filter 1: unknown measurement “precipitation.median”."]);
  expect(errors("p=precipitation.sum:approx:1:in:0.95")).toEqual(["Measured filter 1: unknown comparison “approx”."]);
  expect(errors("p=precipitation.sum:ge:1e3:in:0.95")).toEqual(["Measured filter 1: enter a number (up to six decimal places)."]);
  expect(errors("p=precipitation.sum:ge:1:in:0")).toEqual(["Measured filter 1: minimum coverage must be above 0% and at most 100%."]);
  expect(errors("p=precipitation.sum:ge:1:in:1.5")).toHaveLength(1);
  expect(errors("camera=garage")).toEqual(["Unknown camera “garage”."]);
  expect(errors("segment=dusk")).toEqual(["Unknown segment “dusk”."]);
  expect(parse("from=2026-10-31&to=2026-10-25").errors).toEqual(["The last day is before the first day."]);
  expect(parse("from=2026-02-30&to=2026-03-01").errors).toEqual(["First day “2026-02-30” is not a calendar date."]);
  expect(parse("q=rain").errors).toEqual(["Choose a first day.", "Choose a last day."]);
  expect(parse("from=2026-10-25&to=2026-10-31&units=imperial").errors).toEqual(["Unknown display units “imperial”."]);
  // Measured filters need ≤ 31 days of UTC duration: 31 local days across the fall-back change are 31 d + 1 h.
  expect(parse("from=2026-10-01&to=2026-10-31&p=wind_gust.max:gt:10:mph").errors).toEqual([]);
  expect(parse("from=2026-10-15&to=2026-11-14&p=wind_gust.max:gt:10:mph").errors).toHaveLength(1);
  expect(parse("from=2026-01-01&to=2026-12-31").errors).toEqual([]);
});

test("an invalid draft is written back to the URL exactly as entered", () => {
  const search = "q=rain&from=2026-10-25&to=2026-10-31&camera=garage&p=precipitation.sum%3Age%3Aabc%3Ain%3A0.95&top_k=99&units=us";
  const { draft, query } = parse(search);
  expect(query).toBeNull();
  expect(draft.filters[0].coverage).toBe("95");
  expect(paramsFromDraft(draft).toString()).toBe(search);
});

test("a seek links only inside both the cited interval and the recording; otherwise no link", () => {
  expect(seekLink(hit(1))).toBe("/timelapses/skycam_east/2026-10-31/daytime/?t=2026-10-31T15%3A00%3A00Z");
  expect(seekLink(hit(3))).toBe("/timelapses/skycam_east/2026-10-31/daytime/?t=2026-10-31T12%3A30%3A00Z");
  // The fixture's second hit seeks to 18:00Z, outside its cited 16:00–17:00Z window.
  expect(seekLink(hit(2))).toBeNull();
  expect(seekLink(hit(4))).toBeNull();
  const valid = hit(1);
  const variant = (change: Partial<SearchHit>) => seekLink({ ...valid, ...change });
  // Half-open: the cited window's end instant belongs to the next window.
  expect(variant({ seek: { media_seconds: 12, source_at: "2026-10-31T16:00:00Z" } })).toBeNull();
  expect(variant({ seek: { media_seconds: 12, source_at: "2026-10-31T15:59:59.999999Z" } })).toBe("/timelapses/skycam_east/2026-10-31/daytime/?t=2026-10-31T15%3A59%3A59.999999Z");
  expect(variant({ seek: { media_seconds: 12, source_at: "2026-10-31T09:00:00-06:00" } })).toBe("/timelapses/skycam_east/2026-10-31/daytime/?t=2026-10-31T15%3A00%3A00Z");
  expect(variant({ seek: { media_seconds: 12, source_at: "yesterday" } })).toBeNull();
  // A cited interval outside the recording, or a media offset beyond a segment's timeline.
  expect(variant({ video_interval: { start: "2026-10-31T15:30:00Z", end: "2026-11-01T01:00:00Z" } })).toBeNull();
  expect(variant({ seek: { media_seconds: 120, source_at: "2026-10-31T15:00:00Z" } })).toBeNull();
  expect(variant({ seek: { media_seconds: -1, source_at: "2026-10-31T15:00:00Z" } })).toBeNull();
  expect(variant({ camera_id: "garage" })).toBeNull();
  // An overnight recording belongs to the solar cycle of the evening it starts.
  const overnight = { ...hit(4), seek: { media_seconds: 0, source_at: "2026-11-01T01:00:00Z" } };
  expect(seekLink(overnight)).toBe("/timelapses/frontyard_1/2026-10-31/overnight/?t=2026-11-01T01%3A00%3A00Z");
});

test("hits from one video collapse into one card listing every matched window", () => {
  const groups = groupHits(results.hits);
  expect(groups.map((group) => [group.video.camera_id, group.hits.map((item) => item.rank)])).toEqual([["skycam_east", [1, 3]], ["skycam_west", [2]], ["frontyard_1", [4]]]);
  expect(groups[0].measurements.map((item) => `${item.metric}.${item.statistic}`)).toEqual(["precipitation.sum", "wind_gust.max", "temperature.mean"]);
});

test("unknown or under-covered rain is Unknown, never 0 in; values follow the display units", () => {
  const rain = hit(2).matched_measurements[0];
  expect(rain.value).toBe(0);
  expect(measurementValue(rain, "us", definition("precipitation"))).toBe("Unknown");
  expect(measurementValue(rain, "metric", definition("precipitation"))).toBe("Unknown");
  expect(measurementValue({ ...rain, coverage: { ...rain.coverage, fraction: 0.6 }, value: 2.54 }, "us", definition("precipitation"))).toBe("At least 0.1 in");
  expect(measurementValue({ ...rain, coverage: { ...rain.coverage, fraction: 1 } }, "us", definition("precipitation"))).toBe("0 in");
  const [total, gust, mean] = hit(1).matched_measurements;
  expect(measurementValue(total, "metric", definition("precipitation"))).toBe("1.2 mm");
  expect(measurementValue(gust, "us", definition("wind_gust"))).toBe("28 mph");
  expect(measurementValue(gust, "metric", definition("wind_gust"))).toBe("12.5 m/s");
  expect(measurementValue(mean, "us", definition("temperature"))).toBe("47.1 °F");
  expect(measurementValue({ ...mean, coverage: { ...mean.coverage, fraction: 0.5 } }, "metric", definition("temperature"))).toBe("8.4 °C (partial coverage)");
});

test("responses parse leniently and each status is its own outcome", async () => {
  expect(results.hits).toHaveLength(4);
  const odd = parseSearchResults({ ...fixture, mode: "oracle", degraded_reason: "mystery", extra: 1, hits: [{ ...fixture.hits[0], evidence_precision: "pixel", extra: 1 }, { rank: "x" }] })!;
  expect([odd.mode, odd.degraded_reason, odd.hits.length, odd.hits[0].evidence_precision, "extra" in odd]).toEqual(["lexical", null, 1, "segment", false]);
  expect(parseSearchResults({ hits: "nope" })).toBeNull();
  const badTime = parseSearchResults({ ...fixture, hits: [{ ...fixture.hits[0], video_interval: { start: "soon", end: "later" } }, { ...fixture.hits[1], matched_measurements: [{ ...fixture.hits[1].matched_measurements[0], interval: { start: "x", end: "y" } }] }] })!;
  expect([badTime.hits.length, badTime.hits[0].matched_measurements.length]).toEqual([1, 0]);
  const problem = (status: number, code: string, headers: Record<string, string> = {}) => new Response(JSON.stringify({ code, message: "m" }), { status, headers: { "Content-Type": "application/problem+json", ...headers } });
  expect(await readOutcome(problem(429, "busy", { "Retry-After": "7" }))).toEqual({ kind: "busy", retryAfter: 7 });
  expect(await readOutcome(problem(429, "busy"))).toEqual({ kind: "busy", retryAfter: null });
  expect(await readOutcome(problem(400, "range_too_large"))).toEqual({ kind: "invalid", code: "range_too_large" });
  expect(await readOutcome(problem(400, "something_else"))).toEqual({ kind: "invalid", code: "invalid_request" });
  expect(await readOutcome(problem(503, "unavailable"))).toEqual({ kind: "unavailable" });
  expect(await readOutcome(new Response("<html>", { status: 200 }))).toEqual({ kind: "unavailable" });
  expect(await readOutcome(new Response(JSON.stringify(fixture), { status: 200 }))).toMatchObject({ kind: "results", data: { mode: "hybrid_exact", count: 4 } });
});
