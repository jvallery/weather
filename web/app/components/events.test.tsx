// SPDX-License-Identifier: MIT
import { expect, test } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { createMemoryRouter, RouterProvider } from "react-router";
import { EventStrip, bookmarkInstant, eventDetail } from "./events";
import { parseEvents, parseTimelapses } from "../lib/weather-api";
import catalog from "../fixtures/catalog.json";
import fixture from "../fixtures/events.json";

const feed = parseEvents(fixture)!;
const video = parseTimelapses(catalog)!.items.find((item) => item.camera === "skycam_east" && item.segment === "daytime" && item.cycle_date === "2026-10-31")!;
const byKind = (kind: string) => feed.items.find((event) => event.kind === kind)!;

test("the events fixture parses and unknown enum values degrade to the most cautious label", () => {
  expect(feed.items).toHaveLength(5);
  const unknown = parseEvents({ ...fixture, items: [{ ...fixture.items[0], origin: "oracle", kind: "tornado", extra: 1 }] })!;
  expect(unknown.items[0].origin).toBe("synthesis");
  expect(unknown.items[0].kind).toBe("synthesis");
  expect("extra" in unknown.items[0]).toBe(false);
  expect(parseEvents({ items: "nope" })).toBeNull();
});

test("a bookmark lies inside both the event and the recording", () => {
  const onset = byKind("precipitation_onset");
  const instant = bookmarkInstant(onset, video);
  expect(instant).toBe("2026-10-31T15:20:00.000Z");
  // An event outside the recording has no bookmark rather than a clamped, fabricated one.
  const outside = { ...onset, source_interval: { start: "2026-11-02T00:00:00Z", end: "2026-11-02T00:00:01Z" } };
  expect(bookmarkInstant(outside, video)).toBeNull();
  // An event that began before the recording seeks to the recording's first overlapping instant.
  const early = { ...onset, source_interval: { start: "2026-10-31T12:00:00Z", end: "2026-10-31T13:00:00Z" } };
  expect(bookmarkInstant(early, video)).toBe("2026-10-31T12:30:00.000Z");
});

test("details keep integer strikes, coverage and the report-not-flash caveat", () => {
  expect(eventDetail(byKind("daily_gust_max"))).toBe("14.2 m/s · 31.8 mph observed maximum · gust coverage 98.0% of the day");
  expect(eventDetail(byKind("lightning_strike"))).toBe("1 strike reported · average distance 12 km · a report, not a visible flash");
  expect(eventDetail(byKind("visual"))).toBe("");
});

test("the strip labels provenance and an unavailable API without substitutes", () => {
  const render = (element: React.ReactElement) => {
    const router = createMemoryRouter([{ path: "/", element }]);
    return renderToStaticMarkup(<RouterProvider router={router} />);
  };
  const html = render(<EventStrip title="Events" events={feed.items} video={video} />);
  for (const text of ["Measured · station instrument", "Visual · AI from recorded video", "Manual · human annotation", "5 events", "Seek video to precipitation onset"]) expect(html).toContain(text);
  expect(html).not.toMatch(/\sstyle=/);
  expect(render(<EventStrip title="Events" events={null} />)).toContain("Events API unavailable.");
  expect(render(<EventStrip title="Events" events={[]} />)).toContain("No weather events recorded for this period.");
});
