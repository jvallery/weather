// SPDX-License-Identifier: MIT
import { test, expect, type Page, type Request } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
// Search (#27) against the fixture API's POST /api/v1/search (see e2e/fixture-api.mjs).
const BASE = "from=2026-10-25&to=2026-10-31&units=us";
const control = (page: Page, data: Record<string, unknown>) => page.request.post("http://localhost:4174/__control", { data });
const isSearch = (request: Request) => new URL(request.url()).pathname === "/api/v1/search";
function searchLog(page: Page) {
  const log: Request[] = [];
  page.on("request", (request) => { if (isSearch(request)) log.push(request); });
  return log;
}
const results = (page: Page) => page.locator(".search-result");
const textField = (page: Page) => page.getByLabel(/Words to find/);
const submit = (page: Page) => page.getByRole("button", { name: "Search", exact: true }).click();
test.beforeEach(async ({ page }) => { await control(page, {}); });

for (const width of [320, 768, 1440]) for (const [state, setup, query] of [
  ["results", {}, "q=rain"], ["lexical fallback", { searchOffline: true }, "q=rain"], ["busy", { searchStatus: 429 }, "q=rain"],
  ["unavailable", { searchStatus: 503 }, "q=rain"], ["empty", {}, "q=nothing"], ["invalid URL", {}, "top_k=99"],
] as const) {
  test(`search ${state} · ${width}px · WCAG 2.2 AA`, async ({ page }) => {
    await control(page, setup);
    await page.setViewportSize({ width, height: 900 });
    await page.goto(`/search/?${query}&${BASE}`);
    await expect(page.locator("main [role=status], main [role=alert]").first()).toBeVisible();
    await expect(page.getByText("Searching recordings…")).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
    expect(result.violations).toEqual([]);
  });
}

test("request log: none on load or while typing, exactly one POST per submit, no prefetch, no retry, no cache", async ({ page }) => {
  const log = searchLog(page);
  await page.goto("/search/"); await page.waitForLoadState("networkidle");
  await textField(page).pressSequentially("rain over the ridge", { delay: 20 });
  await page.getByRole("button", { name: "Add measured filter" }).click();
  await page.getByLabel(/^Value\b/).fill("0.1");
  await page.waitForTimeout(500);
  expect(log).toHaveLength(0);
  await submit(page);
  await expect(page.getByText(/^Results for “rain over the ridge”/)).toBeVisible();
  expect(log.map((request) => request.method())).toEqual(["POST"]);
  expect(log[0].postDataJSON()).toMatchObject({ query: "rain over the ridge", predicates: [{ metric: "precipitation", statistic: "sum", op: ">=", value: 0.1, unit: "in", min_coverage: 0.95 }], top_k: 10 });
  await submit(page);
  await expect.poll(() => log.length).toBe(2);
  await page.waitForLoadState("networkidle");
  expect(log).toHaveLength(2);
  expect(await page.evaluate(async () => [localStorage.length, sessionStorage.length, (await navigator.serviceWorker?.getRegistrations())?.length ?? 0])).toEqual([0, 0, 0]);

  await control(page, { searchStatus: 503 });
  await submit(page);
  await expect(page.getByRole("heading", { name: "Search unavailable" })).toBeVisible();
  await page.waitForTimeout(3000);
  expect(log).toHaveLength(3);
  await expect(page.getByText(/No recordings match/)).toHaveCount(0);
  await page.getByRole("button", { name: "Search again" }).click();
  await expect.poll(() => log.length).toBe(4);
});

test("rapid changes abort earlier requests and only the last response renders", async ({ page }) => {
  await page.goto(`/search/?q=rain&${BASE}`);
  await expect(page.getByText(/^Results for “rain”/)).toBeVisible();
  const aborted = page.waitForEvent("requestfailed", (request) => isSearch(request) && request.postDataJSON().query === "slow rain");
  await textField(page).fill("slow rain"); await submit(page);
  await textField(page).fill("slow showers"); await submit(page);
  await textField(page).fill("fast rain"); await submit(page);
  await expect(page.getByText(/^Results for “fast rain”/)).toBeVisible();
  await aborted;
  // Well past the slow responses' 1.5 s: nothing older replaces the newest results.
  await page.waitForTimeout(2500);
  await expect(page.getByText(/^Results for “fast rain”/)).toBeVisible();
  await expect(page.getByText(/“slow/)).toHaveCount(0);
  expect(new URL(page.url()).searchParams.get("q")).toBe("fast rain");
});

test("back and forward restore the filters and their results", async ({ page }) => {
  await page.goto(`/search/?q=rain&camera=skycam_east&${BASE}`);
  await expect(results(page)).toHaveCount(1);
  await textField(page).fill("showers");
  await page.getByRole("checkbox", { name: "East sky" }).uncheck();
  await page.getByRole("checkbox", { name: "West sky" }).check();
  await submit(page);
  await expect(page.getByText(/^Results for “showers” · 1 recording$/)).toBeVisible();
  await expect(results(page).first().getByRole("heading", { level: 3 })).toHaveText(/West sky/);
  await page.goBack();
  await expect(page.getByText(/^Results for “rain” · 1 recording$/)).toBeVisible();
  await expect(textField(page)).toHaveValue("rain");
  await expect(page.getByRole("checkbox", { name: "East sky" })).toBeChecked();
  await expect(page.getByRole("checkbox", { name: "West sky" })).not.toBeChecked();
  await expect(results(page).first().getByRole("heading", { level: 3 })).toHaveText(/East sky/);
  await page.goForward();
  await expect(page.getByText(/^Results for “showers”/)).toBeVisible();
  await expect(page.getByRole("checkbox", { name: "West sky" })).toBeChecked();
});

test("a shared URL with explicit units round-trips into the form, the typed request and back", async ({ page }) => {
  const search = new URLSearchParams([["q", "rain"], ["from", "2026-10-25"], ["to", "2026-10-31"], ["segment", "daytime"], ["p", "precipitation.sum:ge:0.5:in:0.95"], ["p", "wind_gust.max:gt:30:km/h:0.9"], ["p", "temperature.min:lt:0:degC:0.95"], ["top_k", "5"], ["units", "metric"]]).toString();
  const first = page.waitForRequest(isSearch);
  await page.goto(`/search/?${search}`);
  expect((await first).postDataJSON()).toEqual({
    start: "2026-10-25T06:00:00Z", end: "2026-11-01T06:00:00Z", query: "rain", cameras: [], segments: ["daytime"], top_k: 5,
    predicates: [
      { metric: "precipitation", statistic: "sum", op: ">=", value: 0.5, unit: "in", min_coverage: 0.95 },
      { metric: "wind_gust", statistic: "max", op: ">", value: 30, unit: "km/h", min_coverage: 0.9 },
      { metric: "temperature", statistic: "min", op: "<", value: 0, unit: "degC", min_coverage: 0.95 },
    ],
  });
  await expect(page.getByLabel(/^Unit\b/)).toHaveCount(3);
  expect(await page.getByLabel(/^Unit\b/).evaluateAll((items) => items.map((item) => (item as HTMLSelectElement).value))).toEqual(["in", "km/h", "degC"]);
  expect(await page.getByLabel("Minimum coverage (%)").evaluateAll((items) => items.map((item) => (item as HTMLInputElement).value))).toEqual(["95", "90", "95"]);
  await expect(page.getByLabel("Display units")).toHaveValue("metric");
  await expect(page.getByText(/Recordings overlapping 10\/25\/2026, 00:00:00 GMT-06:00 → 11\/01\/2026, 00:00:00 GMT-06:00 \(exclusive\)/)).toBeVisible();
  const again = page.waitForRequest(isSearch);
  await submit(page);
  await again;
  expect(new URL(page.url()).search).toBe(`?${search}`);
});

test("evidence cards: whole-video measurements, unknown rain, and a seek only inside the cited interval", async ({ page }) => {
  await page.goto(`/search/?q=rain&${BASE}`);
  await expect(results(page)).toHaveCount(3);
  const [east, west] = [results(page).nth(0), results(page).nth(1)];
  await expect(east.getByText("Measured · applies to the whole video")).toBeVisible();
  await expect(east).toContainText("Maximum gust: 28 mph");
  await expect(west).toContainText("Rain total: Unknown");
  await expect(west).not.toContainText(/\b0 in\b|no rain/i);
  await expect(west).toContainText("Location unavailable");
  await expect(west.getByRole("link")).toHaveCount(0);
  await east.getByRole("link").first().click();
  await expect(page).toHaveURL(/\/timelapses\/skycam_east\/2026-10-31\/daytime\/\?t=2026-10-31T15%3A00%3A00Z$/);
  // Frame 720 of 3,600 over [12:30Z, 01:00Z): the middle of its display window.
  await expect.poll(() => page.locator("video").evaluate((element: HTMLVideoElement) => element.currentTime)).toBeCloseTo(12.008, 2);
});

test("each mode and failure is a distinct state; numeric, date and camera filters work with inference offline", async ({ page }) => {
  await page.goto(`/search/?q=rain&${BASE}`);
  await expect(page.locator("[data-mode=hybrid_exact]")).toContainText("hybrid exact search");
  await page.goto(`/search/?q=nothing&${BASE}`);
  await expect(page.getByText(/No recordings match this search/)).toBeVisible();

  await control(page, { searchOffline: true });
  await page.goto(`/search/?q=rain&camera=skycam_west&p=precipitation.sum:ge:0:in:0.5&${BASE}`);
  await expect(page.locator("[data-mode=lexical]")).toContainText("AI search is offline");
  await expect(results(page)).toHaveCount(1);
  await page.goto(`/search/?q=%21%21%21&${BASE}`);
  await expect(page.locator("[data-mode=lexical]")).toContainText("(lexical: no terms)");
  await page.goto(`/search/?camera=frontyard_1&segment=overnight&${BASE}`);
  await expect(page.locator("[data-mode=numeric]")).toContainText("newest first");
  await expect(results(page)).toHaveCount(1);

  for (const [status, text] of [[429, "Try again in 7 seconds"], [503, "This is not an empty result"], [400, "Measured filters cover at most 31 days"]] as const) {
    await control(page, { searchStatus: status });
    await page.goto(`/search/?q=rain&${BASE}`);
    await expect(page.getByRole("alert")).toContainText(text);
    await expect(results(page)).toHaveCount(0);
  }
});

test("keyboard only: fill, add a filter and submit, then open a result", async ({ page }) => {
  await page.goto(`/search/?${BASE}`);
  await expect(page.getByText(/^Results for the filters alone/)).toBeVisible();
  await textField(page).focus();
  await page.keyboard.type("rain");
  await page.keyboard.press("Enter");
  await expect(page.getByText(/^Results for “rain”/)).toBeVisible();
  await page.getByRole("button", { name: "Add measured filter" }).focus();
  await page.keyboard.press("Space");
  await expect(page.getByLabel(/^Value\b/)).toBeVisible();
  await page.getByLabel(/^Value\b/).focus();
  await page.keyboard.type("1");
  const searched = page.waitForResponse((response) => isSearch(response.request()));
  await page.keyboard.press("Enter");
  await searched;
  await expect(page).toHaveURL(/p=precipitation\.sum%3Age%3A1%3Ain%3A0\.95/);
  await expect(page.getByText("Searching recordings…")).toHaveCount(0);
  const link = results(page).first().getByRole("link").first();
  await link.focus();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/timelapses\/skycam_east\/2026-10-31\/daytime\/\?t=/);
});
