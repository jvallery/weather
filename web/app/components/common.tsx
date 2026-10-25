// SPDX-License-Identifier: MIT
import type { ReactNode } from "react";
import { Form, Link } from "react-router";
import { CAMERA_LABELS, dateShift, localTime } from "../lib/format";
import type { Timelapse } from "../lib/weather-api";

export function Status({ state, reason }: { state: string; reason?: string | null }) {
  return <span className={`status status-${state}`}><span>{state[0].toUpperCase() + state.slice(1)}</span>{reason && <span> · {reason}</span>}</span>;
}
export function UnitForm({ units, children }: { units: string; children?: ReactNode }) {
  return <Form method="get" className="unit-form"><label htmlFor="units">Display units</label><select key={units} id="units" name="units" defaultValue={units}><option value="us">US customary</option><option value="metric">Metric</option></select>{children}<button type="submit">Apply units</button></Form>;
}
export function VideoLink({ video }: { video: { camera: string; cycle_date: string; segment: Timelapse["segment"] } }) {
  return <Link to={`/timelapses/${video.camera}/${video.cycle_date}/${video.segment}/`}>{CAMERA_LABELS[video.camera as keyof typeof CAMERA_LABELS] ?? video.camera} · {video.cycle_date}{video.segment === "overnight" ? ` → ${dateShift(video.cycle_date, 1)}` : ""} · {video.segment === "daytime" ? "Daytime" : "Overnight"}</Link>;
}
export function SourceInterval({ video }: { video: Pick<Timelapse, "source_interval"> }) {
  return <p className="source-time">Source interval: <time dateTime={video.source_interval.start}>{localTime(video.source_interval.start)}</time> → <time dateTime={video.source_interval.end}>{localTime(video.source_interval.end)}</time> (exclusive) · America/Denver · civil twilight at 6°</p>;
}
