// SPDX-License-Identifier: MIT
import { Link, useLoaderData, type LoaderFunctionArgs } from "react-router";
import plotStyles from "uplot/dist/uPlot.min.css?url";
import { AnalysisPanel, EditorialPanel } from "../components/analysis";
import { SourceInterval, Status, UnitForm } from "../components/common";
import { EventStrip } from "../components/events";
import { LinkedPlayback } from "../components/linked-playback";
import { cameraSchema, segmentSchema } from "../lib/contracts";
import { CAMERA_LABELS, dateShift, localTime } from "../lib/format";
import { catalogRead, eventsRead, metricRegistryRead } from "../lib/public-api.server";
import { calendarDate, unitsFrom } from "../lib/route-params";
import { instant } from "../lib/time-map";
export const meta = () => [{ title: "Recorded timelapse · Weather" }];
export const links = () => [{ rel: "stylesheet", href: plotStyles }];
export async function loader({ request, params }: LoaderFunctionArgs) {
  const camera = cameraSchema.safeParse(params.camera);
  const segment = segmentSchema.safeParse(params.segment);
  if (!camera.success || !segment.success) throw new Response("Unknown recording", { status: 404 });
  const date = calendarDate(params.date || "");
  const url = new URL(request.url);
  const requested = url.searchParams.get("t");
  if (requested !== null && (requested.length > 40 || instant(requested) === null)) throw new Response("Invalid seek time", { status: 400 });
  // An overnight recording spans two calendar days; events are read for both.
  const days = segment.data === "overnight" ? [date, dateShift(date, 1)] : [date];
  const [catalog, registry, ...eventPages] = await Promise.all([
    catalogRead(request, new URLSearchParams({ camera: camera.data, segment: segment.data, cycle_date: date })),
    metricRegistryRead(request),
    ...days.map((day) => eventsRead(request, new URLSearchParams({ day, camera: camera.data, limit: "100" }))),
  ]);
  const video = catalog?.items.find((item) => item.camera === camera.data && item.cycle_date === date && item.segment === segment.data && instant(item.source_interval.start) !== null && instant(item.source_interval.end) !== null) || null;
  const unique = new Map(eventPages.flatMap((page) => page?.items || []).map((event) => [event.id, event]));
  const events = eventPages.some((page) => page === null) ? null : [...unique.values()]
    .filter((event) => !video || (Date.parse(event.source_interval.start) < Date.parse(video.source_interval.end) && Date.parse(event.source_interval.end) > Date.parse(video.source_interval.start)))
    .sort((a, b) => Date.parse(a.source_interval.start) - Date.parse(b.source_interval.start) || (a.id < b.id ? -1 : 1));
  return { camera: camera.data, segment: segment.data, date, video, apiUnavailable: catalog === null, events, requested, registry, units: unitsFrom(url) };
}
export default function VideoPage() {
  const { camera, segment, date, video, apiUnavailable, events, requested, registry, units } = useLoaderData<typeof loader>();
  return <><h1>{CAMERA_LABELS[camera]} · {segment === "daytime" ? "Daytime" : "Overnight"}</h1><p>{date}{segment === "overnight" ? ` → ${dateShift(date, 1)}` : ""} · solar-cycle recording</p>
    <nav className="actions" aria-label="Adjacent recordings"><Link className="button-link" to={`/timelapses/${camera}/${dateShift(date, -1)}/${segment}/`}>Previous date</Link><Link className="button-link" to={`/day/${date}/`}>All cameras for this day</Link><Link className="button-link" to={`/timelapses/${camera}/${dateShift(date, 1)}/${segment}/`}>Next date</Link></nav>
    {!video ? <><p className={apiUnavailable ? "fault" : ""}>{apiUnavailable ? "Recording API unavailable. Source times and playback cannot be confirmed." : "No recording is available. Timelapses are coming soon."}</p><EventStrip title="Weather events on this date" events={events} /></> : <><Status state={video.state} reason={video.reason} /><SourceInterval video={video} />
      <UnitForm units={units}>{requested && <input type="hidden" name="t" value={requested} />}</UnitForm>
      <LinkedPlayback key={`${video.video_id}-${video.revision}`} video={video} requested={requested} events={events} units={units} registry={registry} />
      <h2>Recording metadata</h2><dl className="video-meta"><dt>Frames</dt><dd>{video.allocated_frames.toLocaleString("en-US")} at 60 fps · 7,200 per camera cycle across both segments</dd><dt>Duration</dt><dd>{(video.allocated_frames / 60).toFixed(2)} seconds · accelerated processed imagery</dd><dt>Keogram</dt><dd>{video.keogram_state}</dd><dt>Catalog revision</dt><dd>{video.revision} · updated <time dateTime={video.updated_at}>{localTime(video.updated_at)}</time></dd></dl>
      <h2>{video.analysis.author === "editor" ? "Weather description" : "AI weather description"}</h2><AnalysisPanel analysis={video.analysis} />
      <EditorialPanel editorial={video.editorial} />
    </>}
  </>;
}
