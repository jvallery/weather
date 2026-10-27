// SPDX-License-Identifier: MIT
import { useLoaderData, type LoaderFunctionArgs, type MetaFunction } from "react-router";
import { Link } from "../components/unit-link";
import { AnalysisPanel } from "../components/analysis";
import { PageHead, Pager, SEGMENT_LABELS, Status, VideoLink, videoPath } from "../components/common";
import { EventStrip } from "../components/events";
import { KeogramPanel } from "../components/keogram";
import { HourLinks, StillsNote } from "../components/stills";
import { CAMERA_LABELS, dateShift, dayTitle, localDayStart, shortTime } from "../lib/format";
import { catalogRead, eventsRead, keogramsRead, stillsRead } from "../lib/public-api.server";
import { calendarDate } from "../lib/route-params";
import { playable } from "../lib/weather-api";
export const meta: MetaFunction<typeof loader> = ({ loaderData }) => [{ title: `${loaderData ? dayTitle(loaderData.date) : "Solar cycle"} · Weather` }];
export async function loader({ request, params }: LoaderFunctionArgs) {
  const date = calendarDate(params.date || "");
  const [catalog, events, stills, keograms] = await Promise.all([
    catalogRead(request, new URLSearchParams({ cycle_date: date })),
    eventsRead(request, new URLSearchParams({ day: date, limit: "100" })),
    stillsRead(request, new URLSearchParams({ date })),
    keogramsRead(request, new URLSearchParams({ cycle_date: date })),
  ]);
  return { date, catalog, events: events?.items || null, stills: stills?.items || null, keograms: keograms?.items || null };
}
export default function Day() {
  const { date, catalog, events, stills, keograms } = useLoaderData<typeof loader>();
  return <>
    <PageHead eyebrow={`Solar cycle · ${date}`} title={dayTitle(date)} lede={<>Civil dawn to the next civil dawn. Overnight runs into {dayTitle(dateShift(date, 1), "short")}. <Link to={`/weather/history/?${new URLSearchParams({ start: localDayStart(date), end: localDayStart(dateShift(date, 1)) })}`}>Station history for this day</Link></>}>
      <Pager label="Adjacent solar cycles" previous={`/day/${dateShift(date, -1)}/`} next={`/day/${dateShift(date, 1)}/`} previousLabel="Previous day" nextLabel="Next day" />
    </PageHead>
    {!catalog && <p className="fault">Day metadata API unavailable. Twilight bounds and capture coverage cannot be confirmed.</p>}
    <div className="day-grid">{Object.entries(CAMERA_LABELS).map(([camera, label]) => <section className="card" key={camera} aria-labelledby={`camera-${camera}`}><h2 id={`camera-${camera}`}>{label}</h2>{(["daytime", "overnight"] as const).map((segment) => {
      const video = catalog?.items.find((item) => item.camera === camera && item.segment === segment);
      const keogram = keograms?.find((item) => item.camera === camera && item.segment === segment);
      return <article className="segment-card" key={segment}><h3><span>{SEGMENT_LABELS[segment]}</span>{video && <Status state={video.state} reason={video.reason} />}</h3>
        {video ? <>
          {playable(video) && <Link className="media-tile" to={videoPath(video)} tabIndex={-1} aria-hidden="true"><img src={(video.poster_small ?? video.poster)!.url} alt="" width={(video.poster_small ?? video.poster)!.width ?? undefined} height={(video.poster_small ?? video.poster)!.height ?? undefined} loading="lazy" decoding="async" /><span className="play" /></Link>}
          <p className="small"><VideoLink video={video} /> · <time dateTime={video.source_interval.start}>{shortTime(video.source_interval.start)}</time> to <time dateTime={video.source_interval.end}>{shortTime(video.source_interval.end)}</time></p>
          {keogram && <KeogramPanel keogram={keogram} />}
          <AnalysisPanel analysis={video.analysis} compact />
        </> : <p className="media-empty">No recording yet. Twilight bounds and coverage appear once this segment is captured.</p>}
      </article>;
    })}</section>)}</div>
    <section className="section" aria-labelledby="stills-title">
      <div className="section-head"><h2 id="stills-title">Hourly stills · calendar day {date}</h2></div>
      <StillsNote />
      {!stills ? <p className="fault">Stills API unavailable. No images are shown in its place.</p> : <div className="card stills-strip">{Object.entries(CAMERA_LABELS).map(([camera, label]) => {
        const items = stills.filter((still) => still.camera === camera);
        const archived = items.filter((still) => still.state === "on_target" || still.state === "off_target").length;
        return <div className="stills-row" key={camera}><h3>{label}<small>{archived} of {items.length} hours · <Link to={`/stills/?camera=${camera}&date=${date}`}>View the day&apos;s stills</Link></small></h3><HourLinks camera={camera} date={date} stills={items} /></div>;
      })}</div>}
    </section>
    <EventStrip title={`Weather events · calendar day ${date}`} events={events} />
    <p><Link to={`/events/?day=${date}`}>All events for {date} in the feed →</Link></p>
  </>;
}
