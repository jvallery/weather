// SPDX-License-Identifier: MIT
import { createServer } from "node:http";
import { createReadStream, readFileSync, statSync } from "node:fs";
import { createHash } from "node:crypto";
if (process.platform !== "linux" || process.env.WEATHER_CI_IN_CLUSTER !== "1") throw new Error("Fixture API requires approved in-cluster CI");
const current = JSON.parse(readFileSync(new URL("../app/fixtures/current.json", import.meta.url)));
const history = JSON.parse(readFileSync(new URL("../app/fixtures/history.json", import.meta.url)));
const catalog = JSON.parse(readFileSync(new URL("../app/fixtures/catalog.json", import.meta.url)));
const media = new URL("../.test-media/clip.mp4", import.meta.url);
const poster = new URL("../.test-media/poster.jpg", import.meta.url);
let state = {};
let serial = 0;
function json(res, value, status = 200) { res.writeHead(status, { "Content-Type": "application/json", "Cache-Control": "no-store" }); res.end(JSON.stringify(value)); }
function snapshot() {
  const result = structuredClone(catalog);
  for (const video of result.items) {
    if (state.productState) video.state = state.productState;
    if (state.description) video.editorial.description = state.description;
    for (const rendition of video.renditions) {
      rendition.asset.bytes = statSync(media).size;
      rendition.asset.sha256 = createHash("sha256").update(readFileSync(media)).digest("hex");
      rendition.asset.width = 320; rendition.asset.height = 180;
      rendition.fps = { numerator: 60, denominator: 1 }; rendition.frame_count = 3600;
      rendition.duration = { numerator: 60, denominator: 1 };
    }
    if (video.poster) {
      video.poster.asset.bytes = statSync(poster).size;
      video.poster.asset.sha256 = createHash("sha256").update(readFileSync(poster)).digest("hex");
      video.poster.asset.width = 320; video.poster.asset.height = 180;
    }
    video.allocated_frames = 3600; video.duration = { numerator: 60, denominator: 1 };
    if (!["published", "partial"].includes(video.state)) { video.renditions = []; video.poster = null; video.native_download_url = null; video.reason = "Synthetic fixture lifecycle state"; }
  }
  return result;
}
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
  if (url.pathname === "/api/v1/weather/current") {
    if (state.apiUnavailable) return json(res, { code: "synthetic-unavailable" }, 503);
    const value = structuredClone(current); const now = Date.now();
    value.generated_at = new Date(now).toISOString();
    for (const metric of value.metrics) if (metric.value !== null) {
      const age = metric.freshness === "fresh" ? 5000 : 120_000;
      metric.observed_at = new Date(now - age).toISOString(); metric.received_at = new Date(now - age + 1000).toISOString(); metric.valid_until = new Date(now - age + 90_000).toISOString();
    }
    if (state.unique) { value.station_alias = `synthetic-request-${++serial}`; value.metrics[0].value = serial; }
    return json(res, value);
  }
  if (url.pathname === "/api/v1/weather/observations") return json(res, history);
  if (url.pathname === "/api/v1/timelapses") {
    const value = snapshot();
    for (const key of ["camera", "cycle_date", "segment", "state"]) if (url.searchParams.has(key)) value.items = value.items.filter((video) => video[key] === url.searchParams.get(key));
    return json(res, value);
  }
  if (url.pathname.startsWith("/api/v1/timelapses/")) {
    const value = snapshot().items.find((video) => video.video_id === url.pathname.split("/").at(-1));
    return json(res, value || { code: "not-found" }, value ? 200 : 404);
  }
  if (url.pathname.startsWith("/media/")) return file(req, res, url.pathname.endsWith("poster") ? poster : media, url.pathname.endsWith("poster") ? "image/jpeg" : "video/mp4");
  if (url.pathname.startsWith("/downloads/")) return file(req, res, media, "video/mp4", true);
  json(res, { code: "not-found" }, 404);
}).listen(4174);
