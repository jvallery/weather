// SPDX-License-Identifier: MIT
import { expect, test } from "vitest";
import { withMetric } from "./unit-link";

test("metric carries onto internal links only, keeping the path, query and hash", () => {
  expect(withMetric("/day/2026-10-31/")).toBe("/day/2026-10-31/?units=metric");
  expect(withMetric("?t=2026-10-31T15%3A20%3A00Z")).toBe("?t=2026-10-31T15%3A20%3A00Z&units=metric");
  expect(withMetric("/events/?day=2026-10-31#event-1")).toBe("/events/?day=2026-10-31&units=metric#event-1");
  expect(withMetric("/weather/history/?units=us")).toBe("/weather/history/?units=us");
  expect(withMetric("https://vallery.net/")).toBe("https://vallery.net/");
});
