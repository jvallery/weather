// SPDX-License-Identifier: MIT
import { Link } from "react-router";
import { CAMERA_LABELS, localTime } from "../lib/format";
import type { Still } from "../lib/weather-api";

const STATES: Record<Still["state"], string> = {
  on_target: "On target", off_target: "Off target · nearest capture in the hour", missing: "Missing",
  pending: "Pending selection", withdrawn: "Withdrawn",
};
export function cameraLabel(camera: string): string { return CAMERA_LABELS[camera as keyof typeof CAMERA_LABELS] ?? camera; }
/** "01:00 UTC-06:00", plus "(repeated hour)" for the second 01:00 of the fall change. */
export function slotLabel(still: Pick<Still, "local_label" | "fold">): string {
  return `${still.local_label.slice(11, 16)} UTC${still.local_label.slice(19)}${still.fold ? " (repeated hour)" : ""}`;
}
export function localHour(still: Pick<Still, "local_label">): number { return Number(still.local_label.slice(11, 13)); }
function offsetLabel(seconds: number): string {
  const sign = seconds < 0 ? "−" : "+";
  const value = Math.abs(seconds);
  return `${sign}${value >= 60 ? `${Math.floor(value / 60)} min ${(value % 60).toFixed(1)} s` : `${value.toFixed(1)} s`} from :30`;
}
export function StillCard({ still, heading }: { still: Still; heading: string }) {
  const jpegs = still.images.filter((image) => image.mime === "image/jpeg").sort((a, b) => a.width - b.width);
  const thumbnail = jpegs.find((image) => image.width >= 480) || jpegs[0];
  const full = jpegs.at(-1);
  const source = still.source_interval;
  return <figure className="still card">
    <h3>{heading}</h3>
    {thumbnail && source ? <a href={full?.url || thumbnail.url}><img src={thumbnail.url} width={thumbnail.width} height={thumbnail.height} loading="lazy" alt={`${cameraLabel(still.camera)} at ${localTime(source.start)}`} /></a> : <p className="still-empty">No image</p>}
    <figcaption>
      <p><span className={`status status-${still.state}`}>{STATES[still.state]}{still.reason ? ` · ${still.reason}` : ""}</span></p>
      <p className="source-time">Slot <time dateTime={still.slot_interval.start}>{still.slot_id.slice(11, 16)}</time>–<time dateTime={still.slot_interval.end}>{still.slot_interval.end.slice(11, 16)}</time> UTC · calendar day {still.calendar_date}</p>
      {source && <p className="source-time">Captured <time dateTime={source.start}>{localTime(source.start)}</time>{still.offset_seconds !== null ? ` · ${offsetLabel(still.offset_seconds)}` : ""}</p>}
      {still.cycle_date && <p className="small">Solar cycle {still.cycle_date} · {still.segment === "overnight" ? "overnight" : "daytime"}</p>}
      {full && <p><a href={full.url}>Full size · {full.width} × {full.height} JPEG</a></p>}
    </figcaption>
  </figure>;
}
export function StillsNote() {
  return <p className="small">One native capture per camera per UTC hour, nearest the half hour; originals stay private and these copies carry no metadata. The same clock hour is not the same solar phase: sunrise and sunset move through the year, and daylight saving time shifts the clock by an hour.</p>;
}
export function HourLinks({ camera, date, stills }: { camera: string; date: string; stills: readonly Still[] }) {
  return <ul className="hour-links">{stills.map((still) => <li key={still.slot_id}><Link to={`/stills/?camera=${camera}&date=${date}&hour=${localHour(still)}`} aria-label={`Compare ${cameraLabel(camera)} at ${slotLabel(still)} across dates`}>{slotLabel(still)}</Link> <span className="small">{STATES[still.state]}</span></li>)}</ul>;
}
