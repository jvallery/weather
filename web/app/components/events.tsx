// SPDX-License-Identifier: MIT
import { Link } from "./unit-link";
import { CAMERA_LABELS, localDate, localTime, shortDateTime, shortTime } from "../lib/format";
import type { Timelapse, WeatherEvent } from "../lib/weather-api";

export const ORIGIN_LABELS = { measured: "Measured", visual: "AI · from video", synthesis: "AI · inferred", manual: "Manual note" } as const;
const MPH = 3600 / 1609.344;

/** Provenance is always visible: a measured event is not a visual claim, and AI is never a measurement. */
export function ProvenanceBadge({ event }: { event: WeatherEvent }) {
  return <span className={`badge badge-${event.origin}`}>{ORIGIN_LABELS[event.origin]}{event.human_edited && event.origin !== "manual" ? " · reviewed" : ""}</span>;
}
export function eventDetail(event: WeatherEvent): string {
  const parts: string[] = [];
  if (event.gust_mps !== null) parts.push(`${event.gust_mps.toFixed(1)} m/s · ${(event.gust_mps * MPH).toFixed(1)} mph observed maximum`);
  if (event.strikes !== null) parts.push(`${event.strikes} ${event.strikes === 1 ? "strike" : "strikes"} reported${event.kind === "lightning_interval" ? " in this report interval" : ""}`);
  if (event.distance_km !== null) parts.push(`average distance ${event.distance_km.toFixed(0)} km`);
  if (event.coverage) parts.push(`gust coverage ${(event.coverage.fraction * 100).toFixed(1)}% of the day`);
  if (event.kind === "lightning_strike" || event.kind === "lightning_interval") parts.push("a report, not a visible flash");
  return parts.join(" · ");
}
/** The event's source time, compact: the start, and the end when it differs. */
export function EventTime({ event }: { event: WeatherEvent }) {
  const { start, end } = event.source_interval;
  const precision = event.precision === "second" ? "to the second" : event.precision === "clip" ? "clip window" : "report interval";
  const span = Date.parse(end) - Date.parse(start);
  return <span className="event-when" title={`${localTime(start)} → ${localTime(end)} · ${precision}`}><time dateTime={start}>{shortDateTime(start)}</time>{span > 1000 && <time dateTime={end}>to {localDate(start) === localDate(end) ? shortTime(end) : shortDateTime(end)}</time>}</span>;
}
/** The instant a bookmark seeks to: the start of the event's overlap with this recording, or null. */
export function bookmarkInstant(event: WeatherEvent, video: Pick<Timelapse, "source_interval">): string | null {
  const start = Math.max(Date.parse(event.source_interval.start), Date.parse(video.source_interval.start));
  const end = Math.min(Date.parse(event.source_interval.end), Date.parse(video.source_interval.end));
  return Number.isFinite(start) && Number.isFinite(end) && start < end ? new Date(start).toISOString() : null;
}
function videoLinks(event: WeatherEvent) {
  return event.videos.map((video) => video.state === "available" && video.cycle_date && video.segment && video.seek
    ? <li key={`${video.camera}-${video.video_id}`}><Link to={`/timelapses/${video.camera}/${video.cycle_date}/${video.segment}/?t=${encodeURIComponent(video.seek.source_interval.start)}`}>{CAMERA_LABELS[video.camera]} · {video.segment} video at this time</Link></li>
    : <li key={video.camera} className="muted">{CAMERA_LABELS[video.camera]} · video unavailable</li>);
}
export function EventItem({ event, seekTo, onSeek, showVideos = false }: { event: WeatherEvent; seekTo?: string | null; onSeek?: (event: WeatherEvent) => void; showVideos?: boolean }) {
  const detail = eventDetail(event);
  return <li className="event" id={`event-${event.id}`}>
    <EventTime event={event} />
    <h3>{event.label}</h3>
    <p><ProvenanceBadge event={event} /> {event.camera ? `${CAMERA_LABELS[event.camera]} only` : "Station-wide"}{detail && ` · ${detail}`}</p>
    {seekTo && <p><Link className="seek" to={`?t=${encodeURIComponent(seekTo)}`} onClick={onSeek ? (click) => { click.preventDefault(); onSeek(event); } : undefined}>Seek video to {event.label.toLowerCase()} · {shortTime(seekTo)}</Link></p>}
    {showVideos && event.videos.length > 0 && <ul className="event-links">{videoLinks(event)}</ul>}
  </li>;
}
/** A day's events in source order. Each event is shown once, whatever videos it overlaps. */
/** With `onSeek`, a bookmark seeks the page's player through its time map (the event's interval). */
export function EventStrip({ title, events, unavailable, video, onSeek }: { title: string; events: readonly WeatherEvent[] | null; unavailable?: boolean; video?: Pick<Timelapse, "source_interval">; onSeek?: (event: WeatherEvent) => void }) {
  return <section className="event-strip" aria-labelledby="event-strip-title"><div className="section-head"><h2 id="event-strip-title">{title}</h2>{events && events.length > 0 && <span className="small">{events.length} {events.length === 1 ? "event" : "events"} · times America/Denver</span>}</div>
    {unavailable || events === null ? <p className="fault">Events API unavailable. No events are shown in its place.</p>
      : events.length === 0 ? <p className="muted">No weather events recorded for this period.</p>
      : <ol className="events">{events.map((event) => <EventItem key={event.id} event={event} seekTo={video ? bookmarkInstant(event, video) : null} onSeek={onSeek} />)}</ol>}
  </section>;
}
export function EventFeedItem({ event }: { event: WeatherEvent }) {
  return <EventItem event={event} showVideos />;
}
