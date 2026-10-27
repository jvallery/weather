// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
import { act, cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { createMemoryRouter, RouterProvider } from "react-router";
import { SiteHeader, unitsHref } from "./site-header";
afterEach(cleanup);

test("the units toggle keeps the rest of the URL and only changes units", () => {
  expect(unitsHref("/weather/history/", "?metric=humidity&units=metric", "us")).toBe("/weather/history/?metric=humidity");
  expect(unitsHref("/weather/history/", "?metric=humidity", "metric")).toBe("/weather/history/?metric=humidity&units=metric");
  expect(unitsHref("/", "", "us")).toBe("/");
});

test("the section owning a recording or a day is Timelapses, and metric readers stay metric", async () => {
  const router = createMemoryRouter([{ path: "*", Component: SiteHeader }], { initialEntries: ["/timelapses/skycam_east/2026-10-31/daytime/?units=metric"] });
  try {
    await act(async () => { render(<RouterProvider router={router} />); });
    const nav = within(screen.getByRole("navigation", { name: "Primary" }));
    expect(nav.getByRole("link", { name: "Timelapses" }).getAttribute("aria-current")).toBe("page");
    expect(nav.getByRole("link", { name: "Now" }).getAttribute("aria-current")).toBeNull();
    expect(nav.getByRole("link", { name: "History" }).getAttribute("href")).toBe("/weather/history/?units=metric");
    const units = within(screen.getByRole("navigation", { name: "Measurement system" }));
    expect(units.getByRole("link", { name: /°C/ }).getAttribute("aria-current")).toBe("true");
    expect(units.getByRole("link", { name: /°F/ }).getAttribute("href")).toBe("/timelapses/skycam_east/2026-10-31/daytime/");
  } finally { router.dispose(); }
});
