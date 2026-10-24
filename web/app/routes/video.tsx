// SPDX-License-Identifier: MIT
import { Link, useLoaderData, type LoaderFunctionArgs } from "react-router";
import { Coverage, SourceInterval, Status } from "../components/common";
import { EventStrip } from "../components/events";
import { seekSeconds, VideoPlayer } from "../components/video-player";
import { cameraSchema, segmentSchema } from "../lib/contracts";
import { CAMERA_LABELS, dateShift } from "../lib/format";
import { catalogRead, eventsRead } from "../lib/public-api.server";
import { calendarDate } from "../lib/route-params";
export const meta = () => [{ title: "Recorded timelapse · Weather" }];
export async function loader({ request, params }: LoaderFunctionArgs) {
  const camera = cameraSchema.safeParse(params.camera);
  const segment = segmentSchema.safeParse(params.segment);
  if (!camera.success || !segment.success) throw new Response("Unknown recording", { status: 404 });
  const date = calendarDate(params.date || "");
  const instant = new URL(request.url).searchParams.get("t");
  if (instant !== null && !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?Z$/.test(instant)) throw new Response("Invalid seek time", { status: 400 });
  // An overnight recording spans two calendar days; events are read for both.
  const days = segment.data === "overnight" ? [date, dateShift(date, 1)] : [date];
  const [catalog, ...eventPages] = await Promise.all([
    catalogRead(request, new URLSearchParams({ camera: camera.data, segment: segment.data, cycle_date: date })),
    ...days.map((day) => eventsRead(request, new URLSearchParams({ day, camera: camera.data, limit: "100" }))),
  ]);
  const video = catalog?.items.find((item) => item.camera === camera.data && item.cycle_date === date && item.segment === segment.data) || null;
  const unique = new Map(eventPages.flatMap((page) => page?.items || []).map((event) => [event.id, event]));
  const events = eventPages.some((page) => page === null) ? null : [...unique.values()]
    .filter((event) => !video || (Date.parse(event.source_interval.start) < Date.parse(video.source_interval.end) && Date.parse(event.source_interval.end) > Date.parse(video.source_interval.start)))
    .sort((a, b) => Date.parse(a.source_interval.start) - Date.parse(b.source_interval.start) || (a.id < b.id ? -1 : 1));
  const seekTo = video && instant ? seekSeconds(video, instant) : null;
  return { camera: camera.data, segment: segment.data, date, video, apiUnavailable: catalog === null, events, instant, seekTo };
}
export default function VideoPage() {
  const { camera, segment, date, video, apiUnavailable, events, instant, seekTo } = useLoaderData<typeof loader>();
  return <><h1>{CAMERA_LABELS[camera]} · {segment === "daytime" ? "Daytime" : "Overnight"}</h1><p>{date}{segment === "overnight" ? ` → ${dateShift(date, 1)}` : ""} · solar-cycle recording</p>
    <nav className="actions" aria-label="Adjacent recordings"><Link className="button-link" to={`/timelapses/${camera}/${dateShift(date, -1)}/${segment}/`}>Previous date</Link><Link className="button-link" to={`/day/${date}/`}>All cameras for this day</Link><Link className="button-link" to={`/timelapses/${camera}/${dateShift(date, 1)}/${segment}/`}>Next date</Link></nav>
    {!video ? <><p className={apiUnavailable ? "fault" : ""}>{apiUnavailable ? "Recording API unavailable. Source times and playback cannot be confirmed." : "No recording is available. Timelapses are coming soon."}</p><EventStrip title="Weather events on this date" events={events} /></> : <><Status state={video.state} reason={video.reason} /><SourceInterval video={video} />{instant && seekTo === null && <p className="notice">The requested time is outside this recording or has no mapped frame; playback starts at the beginning.</p>}<VideoPlayer key={`${video.video_id}-${video.media_revision}`} video={video} seekTo={seekTo} />
      <EventStrip title="Weather events in this recording" events={events} video={video} />
      <h2>Recording evidence</h2><p><Coverage fraction={video.coverage.fraction} support={video.coverage.support_seconds} expected={video.coverage.expected_seconds} /></p><dl className="video-meta"><dt>Source time basis</dt><dd>{video.source_time_basis}</dd><dt>Frames</dt><dd>{video.allocated_frames.toLocaleString("en-US")} · 7,200 per camera cycle across both segments</dd><dt>Duration</dt><dd>{(video.duration.numerator / video.duration.denominator).toFixed(2)} seconds · accelerated processed imagery</dd><dt>Slots</dt><dd>{video.accepted_slots.toLocaleString("en-US")} accepted of {video.expected_slots.toLocaleString("en-US")}</dd><dt>Metadata revision</dt><dd>{video.metadata_revision}</dd></dl>
      {video.caveats.length > 0 && <ul>{video.caveats.map((caveat) => <li key={caveat}>{caveat}</li>)}</ul>}
      <h2>Description and provenance</h2>{video.editorial.description ? <p>{video.editorial.description}</p> : <p>No eligible description is available.</p>}<p className="provenance">{video.editorial.human_edited ? "Human-edited interpretation" : "AI interpretation of recorded evidence, not a measurement"} · analysis {video.analysis.state} · execution {video.analysis.execution_state}</p>{video.analysis.limitations.length > 0 && <ul>{video.analysis.limitations.map((limitation) => <li key={limitation}>{limitation}</li>)}</ul>}
    </>}
  </>;
}
