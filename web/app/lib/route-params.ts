// SPDX-License-Identifier: MIT
import { cameraSchema, segmentSchema, type Units } from "./contracts";
export function unitsFrom(url: URL): Units { return url.searchParams.get("units") === "metric" ? "metric" : "us"; }
export function calendarDate(value: string): string {
  const parsed = new Date(`${value}T12:00:00Z`);
  if (!/^\d{4}-\d\d-\d\d$/.test(value) || !Number.isFinite(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== value) throw new Response("Invalid calendar date", { status: 400 });
  return value;
}
export function catalogParams(url: URL): URLSearchParams {
  const result = new URLSearchParams();
  for (const key of ["camera", "segment", "state", "cursor"]) {
    const value = url.searchParams.get(key);
    if (!value) continue;
    if (key === "camera" && !cameraSchema.safeParse(value).success) throw new Response("Invalid camera", { status: 400 });
    if (key === "segment" && !segmentSchema.safeParse(value).success) throw new Response("Invalid segment", { status: 400 });
    if (key === "state" && !["capturing", "queued", "processing", "published", "partial", "unavailable", "expired"].includes(value)) throw new Response("Invalid state", { status: 400 });
    if (value.length > 2048) throw new Response("Invalid filter", { status: 400 });
    result.set(key, value);
  }
  return result;
}
