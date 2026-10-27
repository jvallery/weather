// SPDX-License-Identifier: MIT
import type { ReactNode } from "react";
import { Link } from "react-router";
import { CAMERA_LABELS, dateShift, dayTitle, humanize, localTime } from "../lib/format";
import type { Timelapse } from "../lib/weather-api";

/** A lifecycle state as a tag; its reason code reads as words. */
export function Status({ state, reason }: { state: string; reason?: string | null }) {
  return <span className={`status status-${state}`}><span>{state[0].toUpperCase() + state.slice(1).replaceAll("_", " ")}</span>{reason && <span className="status-reason"> · {humanize(reason)}</span>}</span>;
}
export const SEGMENT_LABELS = { daytime: "Daytime", overnight: "Overnight" } as const;
export const videoPath = (video: { camera: string; cycle_date: string; segment: string }) => `/timelapses/${video.camera}/${video.cycle_date}/${video.segment}/`;
export function VideoLink({ video }: { video: { camera: string; cycle_date: string; segment: Timelapse["segment"] } }) {
  return <Link to={videoPath(video)}>{CAMERA_LABELS[video.camera as keyof typeof CAMERA_LABELS] ?? video.camera} · {video.cycle_date}{video.segment === "overnight" ? ` → ${dateShift(video.cycle_date, 1)}` : ""} · {SEGMENT_LABELS[video.segment]}</Link>;
}
export function SourceInterval({ video }: { video: Pick<Timelapse, "source_interval"> }) {
  return <p className="source-time">Source interval: <time dateTime={video.source_interval.start}>{localTime(video.source_interval.start)}</time> → <time dateTime={video.source_interval.end}>{localTime(video.source_interval.end)}</time></p>;
}
/** The page heading block: eyebrow, title, a short lede and the page's own actions. */
export function PageHead({ eyebrow, title, lede, children }: { eyebrow: string; title: ReactNode; lede?: ReactNode; children?: ReactNode }) {
  return <div className="page-head"><div><span className="eyebrow">{eyebrow}</span><h1>{title}</h1>{lede && <p className="lede">{lede}</p>}</div>{children}</div>;
}
/** Previous / next links for a dated page. */
export function Pager({ label, previous, next, previousLabel, nextLabel }: { label: string; previous: string; next: string; previousLabel: string; nextLabel: string }) {
  return <nav className="pager" aria-label={label}><Link to={previous}>← {previousLabel}</Link><Link to={next}>{nextLabel} →</Link></nav>;
}
export function cycleLabel(date: string, segment: string): string {
  return segment === "overnight" ? `${dayTitle(date, "short")} → ${dayTitle(dateShift(date, 1), "short")}` : dayTitle(date, "short");
}
