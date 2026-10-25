// SPDX-License-Identifier: MIT
import { createServer } from "node:http";
import { createReadStream, readFileSync, statSync } from "node:fs";
import { setTimeout as delay } from "node:timers/promises";
if (process.platform !== "linux" || process.env.WEATHER_CI_IN_CLUSTER !== "1") throw new Error("Fixture API requires approved in-cluster CI");
const current = JSON.parse(readFileSync(new URL("../app/fixtures/current.json", import.meta.url)));
const registry = JSON.parse(readFileSync(new URL("../app/fixtures/metric-registry.json", import.meta.url)));
const aggregates = JSON.parse(readFileSync(new URL("../app/fixtures/aggregates.json", import.meta.url)));
const observations = JSON.parse(readFileSync(new URL("../app/fixtures/observations.json", import.meta.url)));
const catalog = JSON.parse(readFileSync(new URL("../app/fixtures/catalog.json", import.meta.url)));
const almanac = Object.fromEntries(["record", "rain_day", "wind_rose_bin", "astronomy", "comparison", "empty"].map((name) => [name, JSON.parse(readFileSync(new URL(`../app/fixtures/almanac-${name}.json`, import.meta.url)))]));
const events = JSON.parse(readFileSync(new URL("../app/fixtures/events.json", import.meta.url)));
const media = new URL("../.test-media/clip.mp4", import.meta.url);
const poster = new URL("../.test-media/poster.jpg", import.meta.url);
const keogramImage = new URL("../.test-media/keogram.jpg", import.meta.url);
let state = {};
let serial = 0;
function json(res, value, status = 200) { res.writeHead(status, { "Content-Type": "application/json", "Cache-Control": "no-store" }); res.end(JSON.stringify(value)); }
// Synthetic slate ranges (non-uniform) per video; every sidecar reports 95% slot coverage.
const GAPS = { "skycam_east/2026-10-31/daytime": [[1200, 1272], [2000, 2003]], "skycam_west/2026-10-31/overnight": [[600, 601], [1800, 1950]] };
const gcd = (a, b) => (b === 0n ? a : gcd(b, a % b));
function sidecarFor(video) {
  const micros = BigInt(Date.parse(video.source_interval.end) - Date.parse(video.source_interval.start)) * 1000n, denominator = 1_000_000n * BigInt(video.allocated_frames), divisor = gcd(micros, denominator);
  const gaps = GAPS[`${video.camera}/${video.cycle_date}/${video.segment}`] || [];
  return { schema_version: 1, camera: video.camera, cycle_date: video.cycle_date, segment: video.segment, state: video.state, reason: video.reason,
    source_interval: { start: video.source_interval.start.replace("Z", "+00:00"), end: video.source_interval.end.replace("Z", "+00:00") },
    time_map: { frames: video.allocated_frames, fps: 60, timescale: 60000, sample_ticks: 1000, terminal_ticks: video.allocated_frames * 1000, source_step_seconds: { numerator: Number(micros / divisor), denominator: Number(denominator / divisor) } },
    coverage: { slots: { expected: 3600, accepted: 3420, fraction: 0.95, rejected: {}, missing: 180, duplicates: 0, reported_losses: 0 }, exposure: { threshold: "0.9", rendered: video.allocated_frames - gaps.reduce((sum, [a, b]) => sum + b - a, 0), gaps, frames: [] } },
    weather: null };
}
function snapshot() {
  const result = structuredClone(catalog);
  for (const video of result.items) {
    if (state.productState) video.state = state.productState;
    if (state.reason) video.reason = state.reason;
    for (const rendition of video.renditions) rendition.asset.bytes = statSync(media).size;
    if (video.poster) video.poster.bytes = statSync(poster).size;
    if (!["published", "partial"].includes(video.state)) {
      video.renditions = []; video.poster = null; video.native_download_url = null; video.reason ||= "synthetic_state";
      video.keogram = null; video.keogram_state = video.state === "withdrawn" ? "withdrawn" : "unavailable";
      if (video.state === "withdrawn") { video.withdrawn = true; video.sidecar = null; }
    }
  }
  return result;
}
// Public asset IDs resolve only through the current snapshot, as the media origin does (#20).
function asset(id) {
  for (const video of snapshot().items) {
    if (video.renditions.some((item) => item.asset.asset_id === id)) return { path: media, mime: "video/mp4" };
    if (video.poster?.asset_id === id) return { path: poster, mime: "image/jpeg" };
    if (video.keogram?.asset_id === id) return { path: keogramImage, mime: "image/jpeg" };
    if (video.sidecar?.asset_id === id) return { sidecar: sidecarFor(video) };
  }
  return null;
}
// Segment plots (#40): epoch-aligned bins clipped to [start, end), identical for every camera.
// No observations 15:00–15:45Z on 2026-10-31 and 07:45–08:15Z across the 2026-11-01 fall-back hour.
const TELEMETRY_GAPS = [["2026-10-31T15:00:00Z", "2026-10-31T15:45:00Z"], ["2026-11-01T07:45:00Z", "2026-11-01T08:15:00Z"]].map(([a, b]) => [Date.parse(a), Date.parse(b)]);
const UNITS = { temperature: "degC", humidity: "%", station_pressure: "hPa", wind_avg: "m/s", wind_gust: "m/s", precipitation: "mm" };
function segmentAggregates(query) {
  const start = Date.parse(query.get("start")), end = Date.parse(query.get("end")), step = Number(query.get("resolution")) * 1000;
  const items = [];
  for (let lo = Math.floor(start / step) * step; lo < end; lo += step) {
    const a = Math.max(lo, start), b = Math.min(lo + step, end), gap = TELEMETRY_GAPS.some(([x, y]) => a < y && b > x);
    const phase = Math.sin(a / 7_200_000);
    for (const metric of query.getAll("metrics")) for (const statistic of query.getAll("statistics")) {
      const value = { temperature: 8 + 6 * phase, humidity: 55 - 20 * phase, station_pressure: 840 + phase, wind_avg: 3 + phase, wind_gust: 6 + 2 * phase, precipitation: phase > 0.9 ? 0.2 : 0 }[metric];
      items.push({ metric, statistic, start: new Date(a).toISOString(), end: new Date(b).toISOString(), value: gap ? null : Math.round(value * 100) / 100, unit: UNITS[metric] || "1",
        coverage: { fraction: gap ? 0 : 1, support_seconds: gap ? 0 : (b - a) / 1000, expected_seconds: (b - a) / 1000 }, evidence_count: gap ? 0 : Math.round((b - a) / 60000), evidence_hash: null,
        reason: gap ? "No valid observations in this interval." : null, direction_state: null, rose: null });
    }
  }
  return { generated_at: new Date().toISOString(), start: query.get("start"), end: query.get("end"), resolution: query.get("resolution"), items };
}
const segmentWindow = (query) => catalog.items.some((video) => Date.parse(video.source_interval.start) === Date.parse(query.get("start")) && Date.parse(video.source_interval.end) === Date.parse(query.get("end")));
function file(req, res, path, mime, download = false) {
  const size = statSync(path).size;
  const range = req.method === "GET" ? /^bytes=(\d+)-(\d*)$/.exec(req.headers.range || "") : null;
  let start = 0, end = size - 1, status = 200;
  const headers = { "Content-Type": mime, "Accept-Ranges": "bytes", "Cache-Control": "no-store" };
  if (download) headers["Content-Disposition"] = 'attachment; filename="synthetic-recording.mp4"';
  if (range) {
    start = Number(range[1]); end = range[2] ? Math.min(size - 1, Number(range[2])) : size - 1;
    if (start >= size || start > end) { res.writeHead(416, { "Content-Range": `bytes */${size}` }); return res.end(); }
    status = 206; headers["Content-Range"] = `bytes ${start}-${end}/${size}`;
  }
  headers["Content-Length"] = String(end - start + 1);
  res.writeHead(status, headers);
  if (req.method === "HEAD") return res.end();
  createReadStream(path, { start, end }).pipe(res);
}
createServer(async (req, res) => {
  const url = new URL(req.url, "http://localhost");
  if (url.pathname === "/__control" && req.method === "POST") {
    let body = "";
    for await (const chunk of req) { body += chunk; if (body.length > 16_384) { res.writeHead(413); return res.end(); } }
    state = JSON.parse(body || "{}"); serial = 0; return json(res, { ok: true });
  }
  if (url.pathname === "/healthz") return json(res, { ok: true });
  if (state.apiUnavailable && url.pathname.startsWith("/api/v1/weather/")) return json(res, { code: "synthetic-unavailable" }, 503);
  if (state.delayMs && url.pathname.startsWith("/api/")) await delay(Math.min(Number(state.delayMs), 5000));
  if (url.pathname === "/api/v1/weather/current") {
    const value = structuredClone(current); const now = Date.now();
    value.generated_at = new Date(now).toISOString();
    for (const metric of value.metrics) if (metric.value !== null) {
      const age = metric.freshness === "fresh" ? 5000 : 120_000;
      metric.observed_at = new Date(now - age).toISOString(); metric.received_at = new Date(now - age + 1000).toISOString(); metric.valid_until = new Date(now - age + 90_000).toISOString();
    }
    // An unparseable source time makes the page's render fail: a real SSR 500.
    if (state.invalidTime) value.metrics[0].observed_at = "synthetic-invalid-time";
    if (state.unique) { value.station_alias = `synthetic-request-${++serial}`; value.metrics[0].value = serial; }
    return json(res, value);
  }
  if (url.pathname === "/api/v1/weather/metrics") return json(res, registry);
  if (url.pathname === "/api/v1/weather/aggregates") return json(res, segmentWindow(url.searchParams) ? segmentAggregates(url.searchParams) : aggregates);
  if (url.pathname === "/api/v1/weather/observations") return json(res, observations);
  if (url.pathname === "/api/v1/weather/almanac") return json(res, almanac[state.almanacEmpty ? "empty" : url.searchParams.get("product")] || { code: "invalid_request" }, almanac[url.searchParams.get("product")] ? 200 : 400);
  if (url.pathname === "/api/v1/events") {
    // The API's filters: calendar day, camera (plus station-wide), origin; source order.
    const query = url.searchParams;
    let items = events.items.filter((event) => (!query.get("day") || event.local_dates.includes(query.get("day")))
      && (!query.get("camera") || event.camera === null || event.camera === query.get("camera"))
      && (!query.get("origin") || event.origin === query.get("origin")));
    if (query.get("order") === "desc") items = items.reverse();
    return json(res, { ...events, items: items.slice(0, Number(query.get("limit") || 50)) });
  }
  if (url.pathname === "/api/v1/timelapses") {
    const value = snapshot();
    for (const key of ["camera", "cycle_date", "segment", "state"]) if (url.searchParams.has(key)) value.items = value.items.filter((video) => video[key] === url.searchParams.get(key));
    if (url.searchParams.has("month")) value.items = value.items.filter((video) => video.cycle_date.startsWith(`${url.searchParams.get("month")}-`));
    return json(res, value);
  }
  if (url.pathname.startsWith("/api/v1/timelapses/")) {
    const value = snapshot().items.find((video) => video.video_id === url.pathname.split("/").at(-1));
    return json(res, value || { code: "not-found" }, value ? 200 : 404);
  }
  if (url.pathname.startsWith("/media/")) {
    const found = asset(url.pathname.slice("/media/".length));
    if (!found) return json(res, { code: "not_found" }, 404);
    return found.sidecar ? json(res, found.sidecar) : file(req, res, found.path, found.mime);
  }
  if (url.pathname.startsWith("/downloads/")) {
    const id = /^\/downloads\/([0-9a-f-]{36})\/native$/.exec(url.pathname)?.[1];
    return snapshot().items.some((video) => video.video_id === id && video.native_download_url) ? file(req, res, media, "video/mp4", true) : json(res, { code: "not_found" }, 404);
  }
  json(res, { code: "not-found" }, 404);
}).listen(4174);
