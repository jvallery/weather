// SPDX-License-Identifier: MIT
import type { ReactNode } from "react";
import { Form, Link } from "react-router";
import type { Video } from "../lib/contracts";
import { CAMERA_LABELS, dateShift } from "../lib/format";

export function Status({ state, reason }: { state: string; reason?: string | null }) {
  return <span className={`status status-${state}`}><span>{state[0].toUpperCase() + state.slice(1)}</span>{reason && <span> · {reason}</span>}</span>;
}
export function Coverage({ fraction, support, expected }: { fraction: number; support: number; expected: number }) {
  return <span>Coverage {(fraction * 100).toFixed(1)}% · {Math.round(support).toLocaleString("en-US")} of {Math.round(expected).toLocaleString("en-US")} source seconds supported</span>;
}
export function UnitForm({ units, children }: { units: string; children?: ReactNode }) {
  return <Form method="get" className="unit-form"><label htmlFor="units">Display units</label><select key={units} id="units" name="units" defaultValue={units}><option value="us">US customary</option><option value="metric">Metric</option></select>{children}<button type="submit">Apply units</button></Form>;
}
export function VideoLink({ video }: { video: { camera: string; cycle_date: string; segment: Video["segment"] } }) {
  return <Link to={`/timelapses/${video.camera}/${video.cycle_date}/${video.segment}/`}>{CAMERA_LABELS[video.camera as keyof typeof CAMERA_LABELS] ?? video.camera} · {video.cycle_date}{video.segment === "overnight" ? ` → ${dateShift(video.cycle_date, 1)}` : ""} · {video.segment === "daytime" ? "Daytime" : "Overnight"}</Link>;
}
export function SourceInterval({ video }: { video: Video }) {
  return <p className="source-time">Source interval: <time dateTime={video.source_interval.start}>{video.local_start_label}</time> → <time dateTime={video.source_interval.end}>{video.local_end_label}</time> · America/Denver · civil twilight at 6°</p>;
}
