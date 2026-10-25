// SPDX-License-Identifier: MIT
import { describe, expect, test } from "vitest";
import vectors from "../../../docs/contracts/time-model-vectors.json";
import { localTime } from "./format";
import { binCenter, binOf, epochSeconds, frameAt, frameCenter, frameHalfWindowSeconds, frameInterval, frameOf, frameSeconds, gapSpans, instant, iso, isGap, KEOGRAM_BINS, parseTimeMap, resolveSeek, type TimeMap } from "./time-map";

const gcd = (a: bigint, b: bigint): bigint => (b === 0n ? a : gcd(b, a % b));
/** A sidecar exactly as #15 writes it, for [start, end) with N frames and slate ranges. */
function sidecar(start: string, end: string, frames: number, gaps: [number, number][] = [], fraction = 1) {
  const micros = instant(end)! - instant(start)!, denominator = 1_000_000n * BigInt(frames), divisor = gcd(micros, denominator);
  return {
    schema_version: 1, camera: "skycam_east", source_interval: { start, end },
    time_map: { frames, fps: 60, timescale: 60000, sample_ticks: 1000, terminal_ticks: frames * 1000, source_step_seconds: { numerator: Number(micros / divisor), denominator: Number(denominator / divisor) } },
    coverage: { slots: { expected: frames, accepted: Math.round(frames * fraction), fraction }, exposure: { threshold: "0.9", rendered: frames - gaps.reduce((sum, [a, b]) => sum + b - a, 0), gaps, frames: [] } },
  };
}
const build = (start: string, end: string, frames: number, gaps: [number, number][] = []): TimeMap => {
  const map = parseTimeMap(sidecar(start, end, frames, gaps), { source_interval: { start, end }, allocated_frames: frames });
  if (!map) throw new Error("fixture map rejected");
  return map;
};
const A = "2026-10-31T12:30:00Z", B = "2026-11-01T01:00:00Z";
const after = (base: string, seconds: number) => instant(base)! + BigInt(Math.round(seconds * 1e6));

describe("time-model vectors", () => {
  test("frame centres and output PTS (14 h daytime, 4,200 frames)", () => {
    const { source_duration_seconds: duration, frame_count: frames, centers } = vectors.frame_centers;
    const map = build("2026-06-21T10:59:30Z", iso(after("2026-06-21T10:59:30Z", duration)), frames);
    for (const center of centers) {
      const offset = center.source_offset_seconds.numerator / center.source_offset_seconds.denominator;
      const pts = center.output_pts_seconds.numerator / center.output_pts_seconds.denominator;
      expect(frameCenter(map, center.frame)).toBe(after("2026-06-21T10:59:30Z", offset));
      expect(frameOf(map, frameCenter(map, center.frame))).toBe(center.frame);
      // Output time → frame at exact 60 fps; the seek target is that frame's mid-display instant.
      expect(frameAt(map, pts)).toBe(center.frame);
      expect(frameSeconds(map, center.frame) - pts).toBeCloseTo(1 / 120, 12);
      expect(frameAt(map, frameSeconds(map, center.frame))).toBe(center.frame);
    }
  });
  test.each(vectors.allocation)("allocation $name: terminal ticks, round trips and no drift", (row) => {
    for (const [seconds, frames, ticks, output] of [[row.day_seconds, row.day_frames, row.day_terminal_ticks, row.day_output_seconds], [row.night_seconds, row.night_frames, row.night_terminal_ticks, row.night_output_seconds]] as const) {
      const start = "2026-03-20T12:36:16Z", map = build(start, iso(after(start, seconds)), frames);
      expect(frames * map.sampleTicks).toBe(ticks);
      expect(frames / 60).toBeCloseTo(output.numerator / output.denominator, 12);
      // The terminal instant (ended) is the last frame; anything later maps to nothing.
      expect(frameAt(map, ticks / map.timescale)).toBe(frames - 1);
      expect(frameAt(map, (ticks + map.sampleTicks) / map.timescale)).toBeNull();
      for (let frame = 0; frame < frames; frame += 1) {
        const owned = frameInterval(map, frame);
        expect([frameOf(map, owned.start), frameOf(map, owned.end - 1n), frameOf(map, frameCenter(map, frame))]).toEqual([frame, frame, frame]);
        expect(frameAt(map, frameSeconds(map, frame))).toBe(frame);
      }
      expect(frameInterval(map, frames - 1).end).toBe(map.end);
    }
  });
});

describe("non-uniform missing captures", () => {
  // 45,000 s, 3,600 frames of 12.5 s; slates of 1, 36, 3 and 1 frames, the last at the end.
  const gaps: [number, number][] = [[10, 11], [200, 236], [237, 240], [3599, 3600]];
  const map = build(A, B, 3600, gaps);
  test("gap frames keep their own source time and true length; time never compresses", () => {
    expect(gapSpans(map).map((span) => epochSeconds(span.end - span.start))).toEqual([12.5, 450, 37.5, 12.5]);
    expect(gapSpans(map)[1].start).toBe(after(A, 2500));
    expect(gapSpans(map)[3].end).toBe(map.end);
    expect([isGap(map, 199), isGap(map, 200), isGap(map, 235), isGap(map, 236), isGap(map, 237)]).toEqual([false, true, true, false, true]);
    // Frame 236 sits between two slates; it is frame 236, not retained frame 196.
    expect(frameOf(map, after(A, 236 * 12.5 + 6.25))).toBe(236);
    // 10,200 s in is frame 816 whatever was missing before it.
    expect(frameOf(map, after(A, 10200))).toBe(816);
    expect(frameSeconds(map, 816)).toBeCloseTo(816.5 / 60, 12);
    expect(frameHalfWindowSeconds(map)).toBe(6.25);
  });
  test("every frame round-trips output → frame → source → frame", () => {
    for (let frame = 0; frame < map.frames; frame += 1) expect(frameOf(map, frameCenter(map, frame))).toBe(frameAt(map, frameSeconds(map, frame)));
  });
});

describe("boundaries of [start, end)", () => {
  const map = build(A, B, 3600);
  test("before start, at start, last frame, exclusive end and beyond", () => {
    expect(frameOf(map, map.start - 1n)).toBeNull();
    expect(frameOf(map, map.start)).toBe(0);
    expect(frameOf(map, frameInterval(map, 3599).start)).toBe(3599);
    expect(frameOf(map, map.end - 1n)).toBe(3599);
    expect(frameOf(map, map.end)).toBeNull();
    expect(frameOf(map, map.end + 3_600_000_000n)).toBeNull();
    expect(frameAt(map, -0.001)).toBeNull();
    expect(frameAt(map, Number.NaN)).toBeNull();
  });
  test("instant seeks are rejected outside; interval seeks clamp only inside the owned interval", () => {
    expect(resolveSeek(map, map.start - 1n)).toBeNull();
    expect(resolveSeek(map, map.end)).toBeNull();
    expect(resolveSeek(map, map.start)).toEqual({ at: map.start, frame: 0 });
    // An event that began before the recording seeks to the first owned instant.
    expect(resolveSeek(map, { start: map.start - 60_000_000n, end: map.start + 1n })).toEqual({ at: map.start, frame: 0 });
    expect(resolveSeek(map, { start: map.end - 1n, end: map.end + 60_000_000n })).toEqual({ at: map.end - 1n, frame: 3599 });
    // Touching is not overlapping: never sent to the adjacent segment.
    expect(resolveSeek(map, { start: map.start - 60_000_000n, end: map.start })).toBeNull();
    expect(resolveSeek(map, { start: map.end, end: map.end + 1n })).toBeNull();
  });
  test("keogram bins are 1,440 equal UTC bins with the same ownership rule", () => {
    expect(binOf(map, KEOGRAM_BINS, map.start)).toBe(0);
    expect(binOf(map, KEOGRAM_BINS, map.end - 1n)).toBe(1439);
    expect(binOf(map, KEOGRAM_BINS, map.end)).toBeNull();
    expect(binCenter(map, KEOGRAM_BINS, 0)).toBe(after(A, 15.625));
  });
});

describe("sidecar validation", () => {
  const expected = { source_interval: { start: A, end: B }, allocated_frames: 3600 };
  test("a map from another revision or with inexact arithmetic is refused", () => {
    expect(parseTimeMap(sidecar(A, B, 3600), expected)).not.toBeNull();
    expect(parseTimeMap({ ...sidecar(A, B, 3600), extra: true, source_interval: { start: "2026-10-31T12:30:00+00:00", end: "2026-11-01T01:00:00+00:00" } }, expected)?.frames).toBe(3600);
    expect(parseTimeMap(sidecar(A, "2026-11-01T01:00:01Z", 3600), expected)).toBeNull();
    expect(parseTimeMap(sidecar(A, B, 3599), expected)).toBeNull();
    const skewed = sidecar(A, B, 3600); skewed.time_map.source_step_seconds = { numerator: 12, denominator: 1 };
    expect(parseTimeMap(skewed, expected)).toBeNull();
    expect(parseTimeMap(sidecar(A, B, 3600, [[5, 9], [8, 12]]), expected)).toBeNull();
    expect(parseTimeMap(sidecar(A, B, 3600, [[3599, 3601]]), expected)).toBeNull();
    expect(parseTimeMap({ ...sidecar(A, B, 3600), time_map: { ...sidecar(A, B, 3600).time_map, fps: 59.94 } }, expected)).toBeNull();
  });
});

describe("midnight and DST", () => {
  test("an overnight across the fall-back hour: repeated 01:30 has two offsets and two frames", () => {
    const map = build("2026-11-01T01:00:00Z", "2026-11-01T13:30:00Z", 3600);
    const first = instant("2026-11-01T07:30:00Z")!, second = instant("2026-11-01T08:30:00Z")!;
    expect(localTime(iso(first))).toContain("01:30:00 GMT-06:00");
    expect(localTime(iso(second))).toContain("01:30:00 GMT-07:00");
    expect(frameOf(map, second)! - frameOf(map, first)!).toBe(288);
    expect(iso(frameCenter(map, frameOf(map, first)!))).toBe("2026-11-01T07:30:06.25Z");
  });
  test("an overnight across spring-forward and local midnight keeps UTC selection stable", () => {
    const map = build("2026-03-08T00:27:00Z", "2026-03-08T12:52:00Z", 3600);
    const before = instant("2026-03-08T08:59:59Z")!, at = instant("2026-03-08T09:00:00Z")!;
    expect(localTime(iso(before))).toContain("01:59:59 GMT-07:00");
    expect(localTime(iso(at))).toContain("03:00:00 GMT-06:00");
    expect(localTime(iso(instant("2026-03-08T07:00:00Z")!))).toContain("03/08/2026, 00:00:00");
    for (const value of [before, at]) expect(instant(iso(frameCenter(map, frameOf(map, value)!)))).toBe(frameCenter(map, frameOf(map, value)!));
  });
  test("ISO round trips keep microseconds and accept numeric offsets", () => {
    expect(iso(instant("2026-10-31T15:20:00.000Z")!)).toBe("2026-10-31T15:20:00Z");
    expect(iso(instant("2026-10-31T09:20:00.000250-06:00")!)).toBe("2026-10-31T15:20:00.00025Z");
    expect(instant("2026-10-31T15:20:00")).toBeNull();
    expect(instant("yesterday")).toBeNull();
  });
});
