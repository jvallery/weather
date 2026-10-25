// SPDX-License-Identifier: MIT
/** Route-parameter vocabulary and strict UTC intervals; API types live in weather-api.ts (generated). */
import { z } from "zod";
// Zod's caught eval-capability probe still violates a strict browser CSP.
// Configure before constructing any object schema; retain interpreted validation.
z.config({ jitless: true });

const utc = z.string().regex(/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?Z$/).refine((value) => {
  const parsed = new Date(value);
  return Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 19) === value.slice(0, 19);
}, "Invalid UTC instant");
export const cameraSchema = z.enum(["skycam_east", "skycam_west", "frontyard_1"]);
export const segmentSchema = z.enum(["daytime", "overnight"]);
function sourceMicros(value: string): bigint | null {
  const match = /^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d{1,6}))?Z$/.exec(value);
  if (!match) return null;
  const whole = Date.parse(`${match[1]}Z`);
  return Number.isFinite(whole) ? BigInt(whole) * 1000n + BigInt((match[2] || "").padEnd(6, "0")) : null;
}
export const intervalSchema = z.strictObject({ start: utc, end: utc }).refine((x) => {
  const start = sourceMicros(x.start), end = sourceMicros(x.end);
  return start !== null && end !== null && start < end;
}, "Empty or reversed interval");
export type Camera = z.infer<typeof cameraSchema>;
export type Units = "us" | "metric";
