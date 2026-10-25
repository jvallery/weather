// SPDX-License-Identifier: MIT
import { Link, useLoaderData, type LoaderFunctionArgs } from "react-router";
import { AnalysisPanel } from "../components/analysis";
import { SourceInterval, Status, VideoLink } from "../components/common";
import { EventStrip } from "../components/events";
import { HourLinks, StillsNote } from "../components/stills";
import { CAMERA_LABELS, dateShift } from "../lib/format";
import { catalogRead, eventsRead, stillsRead } from "../lib/public-api.server";
import { calendarDate } from "../lib/route-params";
export const meta = () => [{ title: "Solar cycle · Weather" }];
export async function loader({ request, params }: LoaderFunctionArgs) {
  const date = calendarDate(params.date || "");
  const [catalog, events, stills] = await Promise.all([
    catalogRead(request, new URLSearchParams({ cycle_date: date })),
    eventsRead(request, new URLSearchParams({ day: date, limit: "100" })),
    stillsRead(request, new URLSearchParams({ date })),
  ]);
  return { date, catalog, events: events?.items || null, stills: stills?.items || null };
}
export default function Day() {
  const { date, catalog, events, stills } = useLoaderData<typeof loader>();
  return <><h1>Solar cycle · {date}</h1><p>Civil dawn to the following civil dawn. This is a solar cycle, separate from a calendar-day weather summary. Overnight spans {date} → {dateShift(date, 1)}.</p>
    <nav className="actions" aria-label="Adjacent solar cycles"><Link className="button-link" to={`/day/${dateShift(date, -1)}/`}>Previous day</Link><Link className="button-link" to={`/day/${dateShift(date, 1)}/`}>Next day</Link><Link className="button-link" to="/weather/history/">Calendar-day weather history</Link></nav>
    {!catalog && <p className="fault">Day metadata API unavailable. Twilight bounds and capture coverage cannot be confirmed.</p>}
    <div className="grid three">{Object.entries(CAMERA_LABELS).map(([camera, label]) => <section className="card" key={camera}><h2>{label}</h2>{(["daytime", "overnight"] as const).map((segment) => {
      const video = catalog?.items.find((item) => item.camera === camera && item.segment === segment);
      return <article key={segment}><h3>{segment === "daytime" ? "Daytime" : "Overnight"}</h3>{video ? <><p><VideoLink video={video} /></p><Status state={video.state} reason={video.reason} /><SourceInterval video={video} /><p className="small">{video.allocated_frames.toLocaleString("en-US")} frames · keogram {video.keogram_state}</p><AnalysisPanel analysis={video.analysis} compact /></> : <><p>Coming soon · no recording is available.</p><p className="small">Twilight bounds and capture coverage are unavailable until this segment is captured and published.</p></>}</article>;
    })}</section>)}</div>
    <h2>Hourly stills · calendar day {date}</h2><StillsNote />
    {!stills ? <p className="fault">Stills API unavailable. No images are shown in its place.</p> : <div className="grid three">{Object.entries(CAMERA_LABELS).map(([camera, label]) => {
      const items = stills.filter((still) => still.camera === camera);
      const archived = items.filter((still) => still.state === "on_target" || still.state === "off_target").length;
      return <section className="card" key={camera}><h3>{label}</h3><p>{archived} of {items.length} hours archived · <Link to={`/stills/?camera=${camera}&date=${date}`}>View the day's stills</Link></p><HourLinks camera={camera} date={date} stills={items} /></section>;
    })}</div>}
    <EventStrip title={`Weather events · calendar day ${date}`} events={events} />
    <p><Link to={`/events/?day=${date}`}>All events for {date} in the feed</Link></p>
  </>;
}
