// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
import type { ReactElement } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, expect, test } from "vitest";
import { binInterval, binObserved, KeogramPanel, seekHref } from "./keogram";
import { parseKeograms, type KeogramItem } from "../lib/weather-api";
import fixture from "../fixtures/keograms.json";
afterEach(cleanup);
const page = parseKeograms(fixture)!;
const published = page.items.find((k) => k.camera === "skycam_east" && k.segment === "daytime")!;
const partial: KeogramItem = { ...published, state: "partial", reason: "gaps", observed_bins: 1320, gaps: [[600, 720]] };
function show(element: ReactElement) {
  render(<RouterProvider router={createMemoryRouter([{ path: "/", element }, { path: "/timelapses/*", element: <p>recording</p> }])} />);
}
test("bins map exactly onto the segment and seek links stay inside it", () => {
  expect(page.items).toHaveLength(6);
  expect(Date.parse(binInterval(published, 0).start)).toBe(Date.parse(published.bins.start));
  expect(binInterval(published, 1439).end).toBe("2026-11-01T01:00:00.000Z");
  expect(binInterval(published, 721).middle).toBe("2026-10-31T18:45:46.875Z");
  expect(seekHref(published, 0)).toBe("/timelapses/skycam_east/2026-10-31/daytime/?t=2026-10-31T12%3A30%3A15.625Z");
  expect([599, 600, 719, 720].map((index) => binObserved(partial, index))).toEqual([true, false, false, true]);
  for (const keogram of page.items) for (const seconds of keogram.seek?.output_seconds || []) expect(seconds).toBeGreaterThan(0);
});
test("the slider selects bins by keyboard and labels gaps; pending and failed keograms say so", () => {
  show(<KeogramPanel keogram={partial} />);
  const slider = screen.getByRole("slider", { name: "Keogram time" });
  fireEvent.change(slider, { target: { value: "650" } });
  expect(slider.getAttribute("aria-valuetext")).toMatch(/no capture$/);
  expect(screen.getByText(/^Bin 651 of 1440/).textContent).toContain("no capture (gap)");
  expect(screen.getByRole("link", { name: /^Open the recording at/ }).getAttribute("href")).toBe("/timelapses/skycam_east/2026-10-31/daytime/?t=2026-10-31T18%3A08%3A48.125Z");
  cleanup();
  show(<KeogramPanel keogram={{ ...published, state: "failed", reason: "publish_failed", image: null, thumbnail: null }} />);
  expect(screen.getByText(/Failed/)).toBeTruthy();
  expect(screen.queryByRole("img")).toBeNull();
});
