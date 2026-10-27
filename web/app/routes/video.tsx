// SPDX-License-Identifier: MIT
import { Link, useLoaderData, type LoaderFunctionArgs, type MetaFunction } from "react-router";
import plotStyles from "uplot/dist/uPlot.min.css?url";
import { AnalysisPanel, EditorialPanel } from "../components/analysis";
import { cycleLabel, PageHead, Pager, SEGMENT_LABELS, SourceInterval, Status } from "../components/common";
import { EventStrip } from "../components/events";
import { LinkedPlayback } from "../components/linked-playback";
import { cameraSchema, segmentSchema, type Camera, type Segment } from "../lib/contracts";
import { CAMERA_LABELS, dateShift, dayTitle, localTime } from "../lib/format";
import { catalogRead, eventsRead, metricRegistryRead } from "../lib/public-api.server";
import { calendarDate, unitsFrom } from "../lib/route-params";
import { instant } from "../lib/time-map";
import type { Timelapse } from "../lib/weather-api";
export const meta: MetaFunction<typeof loader> = ({ loaderData: data }) => [{ title: data ? `${CAMERA_LABELS[data.camera]} · ${SEGMENT_LABELS[data.segment]} · ${dayTitle(data.date, "short")} · Weather` : "Recorded timelapse · Weather" }];
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

const path = (camera: string, date: string, segment: string, units: string) => `/timelapses/${camera}/${date}/${segment}/${units === "metric" ? "?units=metric" : ""}`;
/** Sibling recordings: the other cameras and segment of this solar cycle. */
function Switcher({ camera, segment, date, units }: { camera: Camera; segment: Segment; date: string; units: string }) {
  return <nav className="switcher" aria-label="Other recordings">
    <div><span className="eyebrow">Camera</span><ul className="chips">{(Object.keys(CAMERA_LABELS) as Camera[]).map((item) => <li key={item}><Link to={path(item, date, segment, units)} aria-current={item === camera ? "page" : undefined}>{CAMERA_LABELS[item]}</Link></li>)}</ul></div>
    <div><span className="eyebrow">Segment</span><ul className="chips">{(["daytime", "overnight"] as const).map((item) => <li key={item}><Link to={path(camera, date, item, units)} aria-current={item === segment ? "page" : undefined}>{SEGMENT_LABELS[item]}</Link></li>)}</ul></div>
    <div><span className="eyebrow">Solar cycle</span><ul className="chips"><li><Link to={`/day/${date}/`}>All cameras for this day</Link></li></ul></div>
  </nav>;
}
function Side({ video, camera, segment, date, units }: { video: Timelapse; camera: Camera; segment: Segment; date: string; units: string }) {
  return <>
    <div className="card"><Switcher camera={camera} segment={segment} date={date} units={units} /></div>
    <div className="card analysis-card"><div className="card-head"><h2>{video.analysis.author === "editor" ? "Weather description" : "AI weather description"}</h2></div><AnalysisPanel analysis={video.analysis} /><EditorialPanel editorial={video.editorial} /></div>
    <div className="card"><div className="card-head"><h2>Recording</h2><Status state={video.state} reason={video.reason} /></div>
      <SourceInterval video={video} />
      <dl className="kv video-meta"><dt>Frames</dt><dd>{video.allocated_frames.toLocaleString("en-US")} at 60 fps ({(video.allocated_frames / 60).toFixed(1)} s)</dd><dt>Keogram</dt><dd>{video.keogram_state}</dd><dt>Revision</dt><dd>{video.revision} · <time dateTime={video.updated_at}>{localTime(video.updated_at)}</time></dd></dl>
      <p className="small">Processed, accelerated imagery: 7,200 frames per camera cycle across both segments.</p>
    </div>
  </>;
}
export default function VideoPage() {
  const { camera, segment, date, video, apiUnavailable, events, requested, registry, units } = useLoaderData<typeof loader>();
  return <>
    <PageHead eyebrow={`Timelapse · ${cycleLabel(date, segment)}`} title={`${CAMERA_LABELS[camera]} · ${SEGMENT_LABELS[segment]}`}>
      <Pager label="Adjacent recordings" previous={path(camera, dateShift(date, -1), segment, units)} next={path(camera, dateShift(date, 1), segment, units)} previousLabel="Previous date" nextLabel="Next date" />
    </PageHead>
    {!video ? <>
      <p className={apiUnavailable ? "fault" : "notice"}>{apiUnavailable ? "Recording API unavailable. Source times and playback cannot be confirmed." : "No recording is available for this camera and segment yet."}</p>
      <div className="card"><Switcher camera={camera} segment={segment} date={date} units={units} /></div>
      <EventStrip title="Weather events on this date" events={events} />
    </> : <LinkedPlayback key={`${video.video_id}-${video.revision}`} video={video} requested={requested} events={events} units={units} registry={registry} side={<Side video={video} camera={camera} segment={segment} date={date} units={units} />} />}
  </>;
}
