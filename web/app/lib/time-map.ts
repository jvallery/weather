// SPDX-License-Identifier: MIT
/**
 * Frame ↔ source-time mapping from a video's sidecar (#15). Segment [a, b) has N frames: frame k
 * owns source time [a + k·(b − a)/N, a + (k + 1)·(b − a)/N), is centred at a + (k + ½)·(b − a)/N and
 * shows from PTS k·1000/60000 s. Arithmetic is exact (integer microseconds as bigint, rational
 * boundaries), never a percentage of the element's duration, a retained-frame index or a nearest
 * sample. Slate (gap) frames keep their own source time: time never compresses.
 */
import { z } from "zod";
z.config({ jitless: true });

export type Instant = bigint;
export type Span = { start: Instant; end: Instant };
const MICROS = 1_000_000n;
const INSTANT = /^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d{1,6}))?(Z|[+-]\d\d:\d\d)$/;

/** A UTC instant (Z or numeric offset, up to microseconds) as integer microseconds, or null. */
export function instant(value: string): Instant | null {
  const match = INSTANT.exec(value);
  if (!match) return null;
  const whole = Date.parse(`${match[1]}${match[3]}`);
  if (!Number.isFinite(whole)) return null;
  return BigInt(whole) * 1000n + BigInt((match[2] || "").padEnd(6, "0"));
}
/** ISO-8601 UTC with a Z suffix and only the fractional digits it needs. */
export function iso(value: Instant): string {
  const seconds = value / MICROS, fraction = value % MICROS;
  const base = new Date(Number(seconds) * 1000).toISOString().slice(0, 19);
  const digits = fraction.toString().padStart(6, "0").replace(/0+$/, "");
  return `${base}${digits ? `.${digits}` : ""}Z`;
}
export function epochSeconds(value: Instant): number { return Number(value) / 1e6; }
export function fromEpochSeconds(value: number): Instant { return BigInt(Math.round(value * 1e6)); }

const ceilDiv = (a: bigint, b: bigint) => (a + b - 1n) / b;

/** Bin i of `count` equal elapsed-UTC bins over [start, end) (frames, keogram columns). */
export function binOf(span: Span, count: number, at: Instant): number | null {
  if (at < span.start || at >= span.end) return null;
  return Number(((at - span.start) * BigInt(count)) / (span.end - span.start));
}
/** The integer-microsecond half-open source interval owned by bin i. */
export function binInterval(span: Span, count: number, index: number): Span {
  const duration = span.end - span.start, n = BigInt(count);
  return { start: span.start + ceilDiv(BigInt(index) * duration, n), end: span.start + ceilDiv(BigInt(index + 1) * duration, n) };
}
export function binCenter(span: Span, count: number, index: number): Instant {
  return span.start + ((2n * BigInt(index) + 1n) * (span.end - span.start)) / (2n * BigInt(count));
}

export type TimeMap = Span & {
  frames: number; timescale: number; sampleTicks: number;
  /** Ordered, disjoint [first, last) slate frame ranges: no observed exposure. */
  gaps: [number, number][];
  /** Share of the segment's capture slots that were accepted, when the sidecar reports it. */
  slotFraction: number | null;
};

const ratio = z.object({ numerator: z.number().int().positive(), denominator: z.number().int().positive() });
const sidecar = z.object({
  schema_version: z.literal(1),
  source_interval: z.object({ start: z.string(), end: z.string() }),
  time_map: z.object({
    frames: z.number().int().positive().max(7200), fps: z.literal(60), timescale: z.literal(60000),
    sample_ticks: z.literal(1000), terminal_ticks: z.number().int(), source_step_seconds: ratio,
  }),
  coverage: z.object({
    slots: z.object({ fraction: z.number().min(0).max(1) }).nullable().catch(null).default(null),
    exposure: z.object({ gaps: z.array(z.tuple([z.number().int(), z.number().int()])).max(7200) }),
  }),
});

/**
 * The sidecar's time map, checked against the catalog entry it was published with (same interval
 * and frame count) and for exact arithmetic (N × step = b − a, N × 1000 ticks). Anything else is
 * null: the page then plays without linking rather than guessing.
 */
export function parseTimeMap(value: unknown, expected: { source_interval: { start: string; end: string }; allocated_frames: number }): TimeMap | null {
  const parsed = sidecar.safeParse(value);
  if (!parsed.success) return null;
  const { source_interval: interval, time_map: map, coverage } = parsed.data;
  const start = instant(interval.start), end = instant(interval.end);
  if (start === null || end === null || start >= end) return null;
  if (start !== instant(expected.source_interval.start) || end !== instant(expected.source_interval.end) || map.frames !== expected.allocated_frames) return null;
  const step = map.source_step_seconds;
  if (BigInt(step.numerator) * MICROS * BigInt(map.frames) !== (end - start) * BigInt(step.denominator)) return null;
  if (map.terminal_ticks !== map.frames * map.sample_ticks) return null;
  const gaps = coverage.exposure.gaps;
  if (gaps.some(([first, last], index) => !(0 <= first && first < last && last <= map.frames) || (index > 0 && gaps[index - 1][1] > first))) return null;
  return { start, end, frames: map.frames, timescale: map.timescale, sampleTicks: map.sample_ticks, gaps, slotFraction: coverage.slots?.fraction ?? null };
}

/** The frame whose source interval contains the instant; null outside [a, b) (never the adjacent segment). */
export function frameOf(map: TimeMap, at: Instant): number | null { return binOf(map, map.frames, at); }
export function frameInterval(map: TimeMap, frame: number): Span { return binInterval(map, map.frames, frame); }
export function frameCenter(map: TimeMap, frame: number): Instant { return binCenter(map, map.frames, frame); }
/** Player seconds for a frame: the middle of its display window, so a seek lands on that frame. */
export function frameSeconds(map: TimeMap, frame: number): number { return (frame * map.sampleTicks + map.sampleTicks / 2) / map.timescale; }
/** The frame shown at a player time; the terminal instant (ended) is the last frame. */
export function frameAt(map: TimeMap, seconds: number): number | null {
  if (!Number.isFinite(seconds) || seconds < 0) return null;
  // A millionth of a frame absorbs float error at exact PTS values (k/60 is not exact in binary).
  const frame = Math.floor((seconds * map.timescale) / map.sampleTicks + 1e-6);
  if (frame < map.frames) return frame;
  return seconds * map.timescale <= map.frames * map.sampleTicks + 1e-3 ? map.frames - 1 : null;
}
export function isGap(map: TimeMap, frame: number): boolean { return map.gaps.some(([first, last]) => first <= frame && frame < last); }
/** Source intervals of the slate ranges, each with its true (uncompressed) length. */
export function gapSpans(map: TimeMap): Span[] {
  return map.gaps.map(([first, last]) => ({ start: frameInterval(map, first).start, end: frameInterval(map, last - 1).end }));
}
/** Half a frame's source window: the precision of any frame-derived instant. */
export function frameHalfWindowSeconds(map: TimeMap): number { return epochSeconds(map.end - map.start) / map.frames / 2; }

/**
 * A seek to an instant, or to an interval (an event), resolves to the instant and the frame that
 * owns it. An interval is clamped to its overlap with [a, b); an instant outside [a, b), or an
 * interval with no overlap, is rejected (null) rather than clamped onto this or the next segment.
 */
export function resolveSeek(map: TimeMap, target: Instant | Span): { at: Instant; frame: number } | null {
  const at = typeof target === "bigint" ? target : target.start > map.start ? target.start : map.start;
  if (typeof target !== "bigint" && (target.end <= map.start || target.start >= map.end || target.start >= target.end)) return null;
  const frame = frameOf(map, at);
  return frame === null ? null : { at, frame };
}

/** Keograms (D10): 1,440 equal elapsed-UTC bins over the segment's [a, b). */
export const KEOGRAM_BINS = 1440;
