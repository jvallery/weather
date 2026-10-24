// SPDX-License-Identifier: MIT
import { Link } from "react-router";
import type { Video } from "../lib/contracts";
import { CAMERA_LABELS, localTime } from "../lib/format";
import type { WeatherEvent } from "../lib/weather-api";

const ORIGIN_LABELS = { measured: "Measured · station instrument", visual: "Visual · AI from recorded video", synthesis: "AI synthesis · inferred", manual: "Manual · human annotation" } as const;
const MPH = 3600 / 1609.344;

/** Provenance is always visible: a measured event is not a visual claim, and AI is never a measurement. */
export function ProvenanceBadge({ event }: { event: WeatherEvent }) {
  return <span className={`badge badge-${event.origin}`}>{ORIGIN_LABELS[event.origin]}{event.human_edited && event.origin !== "manual" ? " · human-reviewed" : ""}</span>;
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
export function EventTime({ event }: { event: WeatherEvent }) {
  const { start, end } = event.source_interval;
  const precision = event.precision === "second" ? "to the second" : event.precision === "clip" ? "clip window" : "report interval";
  return <span className="source-time"><time dateTime={start}>{localTime(start)}</time> → <time dateTime={end}>{localTime(end)}</time> · {precision}</span>;
}
/** The instant a bookmark seeks to: the start of the event's overlap with this recording, or null. */
export function bookmarkInstant(event: WeatherEvent, video: Video): string | null {
  const start = Math.max(Date.parse(event.source_interval.start), Date.parse(video.source_interval.start));
  const end = Math.min(Date.parse(event.source_interval.end), Date.parse(video.source_interval.end));
  return Number.isFinite(start) && Number.isFinite(end) && start < end ? new Date(start).toISOString() : null;
}
function videoLinks(event: WeatherEvent) {
  return event.videos.map((video) => video.state === "available" && video.cycle_date && video.segment && video.seek
    ? <li key={`${video.camera}-${video.video_id}`}><Link to={`/timelapses/${video.camera}/${video.cycle_date}/${video.segment}/?t=${encodeURIComponent(video.seek.source_interval.start)}`}>{CAMERA_LABELS[video.camera]} · {video.segment} video at this time</Link></li>
    : <li key={video.camera}>{CAMERA_LABELS[video.camera]} · video unavailable</li>);
}
export function EventItem({ event, seekTo, showVideos = false }: { event: WeatherEvent; seekTo?: string | null; showVideos?: boolean }) {
  const detail = eventDetail(event);
  return <li className="event" id={`event-${event.id}`}>
    <h3>{event.label}</h3>
    <p><ProvenanceBadge event={event} /> {event.camera ? <span className="small">{CAMERA_LABELS[event.camera]} only</span> : <span className="small">Station-wide</span>}</p>
    <p><EventTime event={event} /></p>
    {detail && <p>{detail}</p>}
    {seekTo && <p><Link className="button-link" to={`?t=${encodeURIComponent(seekTo)}`}>Seek video to {event.label.toLowerCase()} · {localTime(seekTo)}</Link></p>}
    {showVideos && <ul className="segment-list">{videoLinks(event)}</ul>}
  </li>;
}
/** A day's events in source order. Each event is shown once, whatever videos it overlaps. */
export function EventStrip({ title, events, unavailable, video }: { title: string; events: readonly WeatherEvent[] | null; unavailable?: boolean; video?: Video }) {
  return <section className="event-strip" aria-labelledby="event-strip-title"><h2 id="event-strip-title">{title}</h2>
    {unavailable || events === null ? <p className="fault">Events API unavailable. No events are shown in its place.</p>
      : events.length === 0 ? <p>No weather events recorded for this period.</p>
      : <><p className="small">{events.length} {events.length === 1 ? "event" : "events"} · a video link is navigation, not a claim that the camera saw the event.</p><ol className="events">{events.map((event) => <EventItem key={event.id} event={event} seekTo={video ? bookmarkInstant(event, video) : null} />)}</ol></>}
  </section>;
}
export function EventFeedItem({ event }: { event: WeatherEvent }) {
  return <EventItem event={event} showVideos />;
}
