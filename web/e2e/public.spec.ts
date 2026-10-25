// SPDX-License-Identifier: MIT
import { test, expect, type APIResponse, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
const paths = ["/", "/about/", "/archive/?month=2026-10", "/archive/2026/10/", "/day/2026-10-31/", "/timelapses/skycam_east/2026-10-31/daytime/", "/timelapses/skycam_west/2026-10-31/overnight/", "/weather/history/", "/weather/almanac/", "/events/", "/stills/?camera=skycam_east&date=2026-11-01", "/stills/?camera=skycam_east&date=2026-11-01&hour=1", "/search/"];
test.beforeEach(async ({ request, page }) => {
  await page.addInitScript(() => { const violations: string[] = []; (window as Window & { cspViolations?: string[] }).cspViolations = violations; document.addEventListener("securitypolicyviolation", (event) => violations.push(event.effectiveDirective)); }); await request.post("http://localhost:4174/__control", { data: {} }); });
for (const width of [320, 768, 1440]) for (const path of paths) {
  test(`${path} · ${width}px · WCAG 2.2 AA`, async ({ page, browserName }) => {
    await page.setViewportSize({ width, height: 900 });
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    const response = await page.goto(path);
    expect(response?.ok()).toBe(true);
    await page.evaluate(() => document.fonts.ready);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.waitForLoadState("networkidle");
    // Measure the unmodified application before axe mutates the document.
    expect(await response?.text()).not.toMatch(/\sstyle\s*=/i);
    // WebKit 27.2 creates one forbidden inline style inside each native select's
    // user-agent shadow tree (upstream327212/PW43497). The control below verifies
    // this exact engine defect and a real injected-style negative control.
    const nativeSelectCount = browserName === "webkit" ? await page.locator("select").count() : 0;
    expect(await page.evaluate(() => (window as Window & { cspViolations?: string[] }).cspViolations)).toEqual(Array(nativeSelectCount).fill("style-src-attr"));
    const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
    expect(result.violations).toEqual([]);
    expect(errors).toEqual([]);
    // axe's elementsFromPoint polyfill probes inline pointer-event styles in
    // WebKit. Keep all AA rules; exclude only its post-scan style-attribute noise.
    expect(await page.evaluate(() => (window as Window & { cspViolations?: string[] }).cspViolations?.filter((directive) => directive !== "style-src-attr"))).toEqual([]);
    expect(response?.headers()["content-security-policy"]).not.toContain("unsafe-inline");
    expect(response?.headers()["x-frame-options"]).toBe("DENY");
    if (["/", "/about/", "/weather/history/"].includes(path)) await page.screenshot({ path: `../artifacts/synthetic-${path === "/" ? "today" : path.includes("history") ? "history" : "about"}-${width}-${test.info().project.name}.png`, fullPage: true });
  });
}
test("almanac: empty history passes axe, and every product has a text alternative", async ({ page, request }) => {
  await request.post("http://localhost:4174/__control", { data: { almanacEmpty: true } });
  await page.goto("/weather/almanac/"); await page.waitForLoadState("networkidle");
  await expect(page.getByText(/No station history yet/).first()).toBeVisible();
  expect((await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze()).violations).toEqual([]);
  await request.post("http://localhost:4174/__control", { data: {} });
  await page.goto("/weather/almanac/"); await expect(page.getByText(/Station history since 2026-10-03/)).toBeVisible();
  for (const [product, table] of [["rain_day", "Rain by local day"], ["wind_rose_bin", "Wind rose table"], ["astronomy", "Sun and moon times"], ["comparison", "Period comparison"]]) {
    await page.goto(`/weather/almanac/?product=${product}`);
    await expect(page.getByRole("region", { name: table })).toBeVisible();
  }
  await expect(page.locator("svg.wind-rose")).toHaveCount(0);
  await page.goto("/weather/almanac/?product=wind_rose_bin"); await expect(page.getByRole("img", { name: /Wind rose/ })).toBeVisible();
});
test("native select CSP control and real inline-style negative control", async ({ page, browserName }) => {
  const csp = "default-src 'self'; script-src 'self'; style-src 'self'";
  await page.route("**/__csp-control/*", async (route) => {
    const name = new URL(route.request().url()).pathname.split("/").at(-1);
    const select = name === "empty" ? "" : "<select aria-label='Native control'><option>One</option></select>";
    const injection = name === "injected" ? "<p style='color:red'>Forbidden application style</p>" : "";
    await route.fulfill({ contentType: "text/html", headers: { "Content-Security-Policy": csp }, body: `<!doctype html><html lang="en"><head><title>CSP control</title></head><body>${select}${injection}</body></html>` });
  });
  for (const name of ["empty", "native", "injected"]) {
    await page.goto(`/__csp-control/${name}`);
    await page.waitForLoadState("networkidle");
    const native = browserName === "webkit" && name !== "empty" ? 1 : 0;
    const expected = native + (name === "injected" ? 1 : 0);
    expect(await page.evaluate(() => (window as Window & { cspViolations?: string[] }).cspViolations)).toEqual(Array(expected).fill("style-src-attr"));
  }
});
test("without JavaScript: archive to day to video, times, text and native download", async ({ browser }) => {
  const context = await browser.newContext({ javaScriptEnabled: false });
  const page = await context.newPage(); await page.goto("http://localhost:4173/archive/?month=2026-10");
  await page.getByRole("link", { name: "Open solar cycle 2026-10-31", exact: true }).click();
  await page.getByRole("link", { name: /East sky · 2026-10-31 · Daytime/ }).click();
  await expect(page.getByRole("heading", { name: /East sky · Daytime/ })).toBeVisible();
  await expect(page.getByText(/Source interval:/)).toBeVisible();
  await expect(page.getByRole("link", { name: /Download native/ })).toBeVisible();
  await context.close();
});
test("placeholders, About topics, repeated hour, source-only network and reduced motion", async ({ page }) => {
  const unexpected: string[] = [];
  page.on("request", (request) => { const url = new URL(request.url()); if (url.protocol !== "blob:" && url.protocol !== "data:" && !url.hostname.includes("localhost")) unexpected.push(request.url()); });
  await page.emulateMedia({ reducedMotion: "reduce" }); await page.goto("/");
  // Delayed previews (#39): honest capture age, stale and unavailable tiles.
  const tiles = page.locator("[data-preview-state]");
  await expect(tiles).toHaveCount(3);
  await expect(tiles.nth(0)).toHaveAttribute("data-preview-state", "fresh");
  await expect(page.getByRole("img", { name: /^East sky camera, captured \d\d:\d\d \(11 min ago\)$/ })).toBeVisible();
  await expect(tiles.nth(1)).toHaveAttribute("data-preview-state", "stale");
  await expect(tiles.nth(1).getByText("Stale", { exact: true })).toBeVisible();
  await expect(tiles.nth(1).getByText(/captured \d\d:\d\d \(25 min ago\)/)).toBeVisible();
  await expect(tiles.nth(2)).toHaveAttribute("data-preview-state", "unavailable");
  await expect(tiles.nth(2).getByText("Preview unavailable. No eligible preview yet.")).toBeVisible();
  await expect(tiles.nth(2).getByRole("img")).toHaveCount(0);
  // Today media tiles (#40): the latest poster/video and keogram per camera and segment; no latest-media placeholder.
  await expect(page.getByRole("link", { name: /^Poster frame, .*Watch/ })).toHaveCount(6);
  await expect(page.getByRole("link", { name: "Keogram, East sky · daytime · 2026-10-31" })).toBeVisible();
  await expect(page.getByText("Keogram unavailable.", { exact: true })).toHaveCount(1);
  await expect(page.getByText("Keogram pending.", { exact: true })).toHaveCount(4);
  await expect(page.getByText(/latest video · coming soon/)).toHaveCount(0);
  await page.getByRole("link", { name: /^Poster frame, West sky · overnight/ }).click();
  await expect(page).toHaveURL(/\/timelapses\/skycam_west\/2026-10-31\/overnight\/$/);
  await page.goto("/");
  expect(await page.evaluate(() => document.getAnimations().length)).toBe(0);
  await page.goto("/about/");
  for (const topic of [/Tempest/, /east sky/, /west sky/, /front yard/, /6°/, /7,200/, /not measurements/, /private notes/, /no forecast/]) await expect(page.getByText(topic).first()).toBeVisible();
  await page.goto("/weather/history/"); await expect(page.getByText(/GMT-06:00/).first()).toBeVisible(); await expect(page.getByText(/GMT-07:00/).first()).toBeVisible();
  expect(unexpected).toEqual([]);
});
test("preview tiles age locally past 1,800 s while the network is blocked", async ({ page }) => {
  await page.clock.install();
  await page.goto("/");
  const east = page.locator("[data-preview-state]").first();
  await expect(east).toHaveAttribute("data-preview-state", "fresh");
  await page.route(/\/api\/v1\/cameras\//, (route) => route.abort());
  // 700 s at receipt + 10 min = 1,300 s: stale. + 20 min = 1,900 s: unavailable, no image.
  await page.clock.fastForward("10:00");
  await expect(east).toHaveAttribute("data-preview-state", "stale");
  await page.clock.fastForward("10:00");
  await expect(east).toHaveAttribute("data-preview-state", "unavailable");
  await expect(east.getByRole("img")).toHaveCount(0);
  await expect(east.getByText("Preview unavailable. The last capture is more than 30 minutes old.")).toBeVisible();
});
test("event bookmarks seek via ?t=, day and video strips, feed provenance and recent events", async ({ page }) => {
  await page.goto("/timelapses/skycam_east/2026-10-31/daytime/");
  const strip = page.getByRole("region", { name: "Weather events in this recording" });
  // Station-wide events plus this camera's; the west-sky and front-yard events are not here.
  await expect(strip.getByRole("listitem")).toHaveCount(3);
  await expect(strip.getByText("Clouds clearing")).toHaveCount(0);
  const video = page.locator("video");
  await expect.poll(() => video.evaluate((element: HTMLVideoElement) => element.readyState)).toBeGreaterThanOrEqual(1);
  expect(await video.evaluate((element: HTMLVideoElement) => element.currentTime)).toBe(0);
  // 15:20Z is 10,200 s into the 45,000 s source interval: frame 816 of 3,600 at 60 fps.
  await strip.getByRole("link", { name: /^Seek video to precipitation onset/ }).click();
  await expect(page).toHaveURL(/\?t=2026-10-31T15%3A20%3A00Z$/);
  await expect.poll(() => video.evaluate((element: HTMLVideoElement) => element.currentTime)).toBeCloseTo(13.6, 1);
  expect(await video.evaluate((element: HTMLVideoElement) => element.paused)).toBe(true);
  // A shared bookmark URL seeks on load: 00:40:12Z maps to frame 3,504.
  await page.goto("/timelapses/skycam_east/2026-10-31/daytime/?t=2026-11-01T00:40:12.000Z");
  await expect.poll(() => page.locator("video").evaluate((element: HTMLVideoElement) => element.currentTime)).toBeCloseTo(58.4, 1);
  await expect(page.getByText("a report, not a visible flash", { exact: false }).first()).toBeVisible();
  // Out-of-recording times start at the beginning and say so; malformed times are rejected.
  await page.goto("/timelapses/skycam_east/2026-10-31/daytime/?t=2026-10-30T00:00:00Z");
  await expect(page.getByText(/outside this recording/)).toBeVisible();
  expect((await page.request.get("/timelapses/skycam_east/2026-10-31/daytime/?t=yesterday")).status()).toBe(400);

  await page.goto("/day/2026-10-31/");
  const day = page.getByRole("region", { name: "Weather events · calendar day 2026-10-31" });
  await expect(day.getByRole("listitem")).toHaveCount(5);
  await expect(day.getByText("14.2 m/s · 31.8 mph observed maximum", { exact: false })).toBeVisible();

  await page.goto("/events/");
  for (const badge of ["Measured · station instrument", "Visual · AI from recorded video", "Manual · human annotation"]) await expect(page.getByText(badge).first()).toBeVisible();
  await expect(page.locator(".badge")).toHaveCount(5);
  await page.getByLabel("Origin").selectOption("manual");
  await page.getByRole("button", { name: "Apply filters" }).click();
  await expect(page).toHaveURL(/origin=manual/);
  await expect(page.locator("li.event")).toHaveCount(1);
  await expect(page.getByRole("heading", { name: "Hail on the deck" })).toBeVisible();
  await page.goto("/events/");
  await page.getByRole("link", { name: "East sky · daytime video at this time" }).click();
  await expect(page).toHaveURL(/\/timelapses\/skycam_east\/2026-10-31\/daytime\/\?t=2026-10-31T15%3A20%3A00Z$/);
  await expect.poll(() => page.locator("video").evaluate((element: HTMLVideoElement) => element.currentTime)).toBeCloseTo(13.6, 1);

  await page.goto("/");
  const recent = page.getByRole("region", { name: "Recent weather events" });
  await expect(recent.getByRole("listitem")).toHaveCount(5);
  await expect(recent.getByRole("heading", { level: 3 }).first()).toHaveText("Lightning strike reported");
});
test("safe interpretation text, lifecycle states and quality preservation", async ({ page, request }) => {
  await request.post("http://localhost:4174/__control", { data: { reason: "<script>window.SYNTHETIC_XSS=1</script>" } });
  await page.goto("/timelapses/skycam_east/2026-10-31/daytime/");
  expect(await page.evaluate(() => (window as Window & { SYNTHETIC_XSS?: number }).SYNTHETIC_XSS)).toBeUndefined();
  await expect(page.getByText("<script>window.SYNTHETIC_XSS=1</script>")).toBeVisible();
  const video = page.locator("video"); expect(await video.getAttribute("autoplay")).toBeNull();
  await expect.poll(() => video.evaluate((element: HTMLVideoElement) => element.readyState)).toBeGreaterThanOrEqual(1);
  expect(await video.evaluate((element: HTMLVideoElement) => element.paused)).toBe(true);
  await video.evaluate((element: HTMLVideoElement) => { element.currentTime = 12; });
  const playing = () => video.evaluate((element: HTMLVideoElement) => element.currentSrc && new URL(element.currentSrc).pathname);
  const webSource = await playing();
  expect(webSource).toMatch(/^\/media\/[0-9a-f-]{36}$/);
  await page.getByLabel("Playback quality").selectOption("mobile");
  await expect.poll(playing).not.toBe(webSource);
  await expect.poll(playing).toMatch(/^\/media\/[0-9a-f-]{36}$/);
  await expect.poll(() => video.evaluate((element: HTMLVideoElement) => element.currentTime)).toBeCloseTo(12, 0);
  // A partial-coverage recording stays playable and says so (C6).
  await request.post("http://localhost:4174/__control", { data: { productState: "partial" } });
  await page.reload(); await expect(page.locator("video")).toHaveCount(1);
  await expect(page.getByText(/^Partial coverage · 95\.0% of the source interval was captured/)).toBeVisible();
  await expect(page.getByText(/^Partial/).first()).toBeVisible();
  await expect.poll(() => video.evaluate((element: HTMLVideoElement) => element.readyState)).toBeGreaterThanOrEqual(1);
  for (const state of ["unavailable", "withdrawn"]) {
    await request.post("http://localhost:4174/__control", { data: { productState: state } });
    await page.reload(); await expect(page.locator("video, main img")).toHaveCount(0);
    await expect(page.getByText(new RegExp(`^${state[0].toUpperCase() + state.slice(1)}`)).first()).toBeVisible();
  }
});
test("AI description: label, model/prompt provenance, state and coverage on video and day pages", async ({ page, request }) => {
  const cases = [["complete", "Complete"], ["degraded", "Degraded"], ["pending", "Pending"], ["failed", "Failed"]] as const;
  for (const [state, label] of cases) {
    await request.post("http://localhost:4174/__control", { data: { analysis: state } });
    for (const path of ["/timelapses/skycam_east/2026-10-31/daytime/", "/day/2026-10-31/"]) {
      const response = await page.goto(path);
      await expect(page.getByText("AI-generated · sampled-frame analysis").first()).toBeVisible();
      await expect(page.getByText(new RegExp(`· ${label}`)).first()).toBeVisible();
      // Only validated fields render; an unknown raw field in the API response never does.
      expect(await response?.text()).not.toContain("RAW-MODEL-TEXT-SENTINEL");
    }
    await page.goto("/timelapses/skycam_east/2026-10-31/daytime/");
    if (state === "complete" || state === "degraded") {
      await expect(page.getByText(/^Model llm\.primary\.longctx\.mm \(synthetic-model\) · prompt 1f2e3d4c5b6a7980/)).toBeVisible();
      await expect(page.getByText(/^Analysis coverage: 88% of the source interval reviewed · 11 of 13 hour windows/)).toBeVisible();
      await expect(page.getByText(state === "complete" ? "Visual" : "Measured", { exact: true }).first()).toBeVisible();
    } else {
      await expect(page.getByText("No AI description is available.")).toBeVisible();
      await expect(page.getByText(/retried nightly for 7 days/)).toBeVisible();
    }
  }
  // Description text is escaped text, never markup.
  await request.post("http://localhost:4174/__control", { data: { analysis: "complete", description: "<script>window.SYNTHETIC_XSS=1</script>" } });
  await page.goto("/timelapses/skycam_east/2026-10-31/daytime/");
  expect(await page.evaluate(() => (window as Window & { SYNTHETIC_XSS?: number }).SYNTHETIC_XSS)).toBeUndefined();
  await expect(page.getByText("<script>window.SYNTHETIC_XSS=1</script>")).toBeVisible();
});
test("concurrent SSR requests have isolated query state and expose no private upstream", async ({ request }) => {
  await request.post("http://localhost:4174/__control", { data: { unique: true } });
  const bodies = await Promise.all(Array.from({ length: 4 }, async () => (await request.get("http://localhost:4173/")).text()));
  const rendered = bodies.map((body) => /<h3>Temperature<\/h3><strong>([^<]+)<\/strong>/.exec(body)?.[1]);
  expect(rendered.every(Boolean)).toBe(true); expect(new Set(rendered).size).toBe(4);
  expect(bodies.every((body) => !body.includes("http://api") && !body.includes("localhost:4174"))).toBe(true);
});

test("About is complete without JavaScript", async ({ browser }) => {
  const context = await browser.newContext({ javaScriptEnabled: false });
  const page = await context.newPage(); await page.goto("http://localhost:4173/about/");
  for (const topic of [/Tempest/, /east sky/, /west sky/, /front yard/, /Measured values/, /Derived values/, /AI descriptions/, /Human-edited/, /6°/, /7,200/, /not measurements/, /private notes/, /at least 10 minutes/, /no forecast/, /person identification/]) await expect(page.getByText(topic).first()).toBeVisible();
  await context.close();
});
test("public client assets contain no private upstream or source maps", async ({ page, request }) => {
  await page.goto("/");
  const urls = await page.evaluate(() => [...new Set([...performance.getEntriesByType("resource").map((entry) => entry.name)])].filter((url) => new URL(url).pathname.startsWith("/assets/") && /\.(js|css)$/.test(new URL(url).pathname)));
  expect(urls.length).toBeGreaterThan(0);
  for (const url of urls) {
    const response = await request.get(url); expect(response.ok()).toBe(true);
    expect(await response.text()).not.toMatch(/http:\/\/(?:api(?::8000)?|localhost:4174)|API_ORIGIN|sourceMappingURL=/);
    expect((await request.get(`${url}.map`)).status()).toBe(404);
  }
});

test("degraded SSR is observable and public errors stay sanitized", async ({ page, request }) => {
  await request.post("http://localhost:4174/__control", { data: { apiUnavailable: true } });
  await page.goto("/"); await expect(page.getByText(/API unavailable/)).toBeVisible();
  await page.goto("/weather/history/"); await expect(page.getByText(/History API unavailable/)).toBeVisible();
  await expect(page.getByText("Export is unavailable until observations can be read.")).toBeVisible();
  await expect(page.getByRole("link", { name: /Export raw observations/ })).toHaveCount(0);
  const metrics = await request.get("http://localhost:9175/metrics"); expect(metrics.ok()).toBe(true);
  expect(await metrics.text()).toMatch(/weather_web_api_requests_total\{route="\/api\/v1\/weather\/current",result="http_error"\} [1-9]/);
  expect((await request.get("/metrics")).status()).toBe(404);
  for (const path of ["/missing/", "/day/invalid/", "/weather/history/?start=invalid"]) {
    const response = await request.get(path); expect([400, 404]).toContain(response.status());
    expect(response.headers()["content-security-policy"]).not.toContain("unsafe-inline");
    expect(response.headers()["x-frame-options"]).toBe("DENY");
    expect(await response.text()).not.toMatch(/http:\/\/api|localhost:4174|API_ORIGIN|node_modules|Error:|\bat [A-Za-z].*\.js:/);
  }
});
test("SSR admission: loader-less routes and the route manifest stay available while API-backed slots are full", async ({ page, request }) => {
  // Hydrate first: the later navigation is a client-side single-fetch .data request.
  await page.goto("/about/");
  const version = await page.evaluate(() => (window as Window & { __reactRouterManifest?: { version: string } }).__reactRouterManifest?.version);
  expect(version).toBeTruthy();
  await request.post("http://localhost:4174/__control", { data: { delayMs: 5000 } });
  const held = Array.from({ length: 4 }, () => request.get("/"));
  await expect.poll(async () => (await request.get("http://localhost:9175/metrics")).text()).toMatch(/^weather_web_ssr_inflight 4$/m);
  const busy = await request.get("/archive/");
  expect(busy.status()).toBe(503); expect(busy.headers()["retry-after"]).toBe("5"); expect(await busy.text()).toMatch(/busy/i);
  for (const path of ["/about/", "/search/"]) expect((await request.get(path)).status()).toBe(200);
  // The lazy route manifest request exactly as the fog-of-war client sends it.
  const manifest = await request.get(`/__manifest?paths=${encodeURIComponent("/search/")}&version=${encodeURIComponent(version || "")}`);
  expect(manifest.status()).toBe(200);
  expect(await manifest.json()).toMatchObject({ "routes/search": { path: "search" } });
  await page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Archive" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Weather is busy" })).toBeVisible();
  await expect(page.getByText(/Please retry in a few seconds/)).toBeVisible();
  for (const response of await Promise.all(held)) expect(response.status()).toBe(200);
});
test("a failed lazy chunk reloads the page exactly once", async ({ page, request }) => {
  // Every module the document names loads; the lazily imported chart chunk is gone, as after a rollout.
  const documentAssets = new Set([...(await (await request.get("/weather/history/")).text()).matchAll(/\/assets\/[^"'\s]+\.js/g)].map((match) => match[0]));
  let aborted = 0, loads = 0;
  page.on("load", () => { loads += 1; });
  await page.route(/\/assets\/[^/]+\.js$/, (route) => {
    if (documentAssets.has(new URL(route.request().url()).pathname)) return route.continue();
    aborted += 1; return route.abort();
  });
  await page.goto("/weather/history/");
  await expect.poll(() => loads).toBe(2);
  await page.waitForTimeout(3000);
  expect(loads).toBe(2); expect(aborted).toBeGreaterThanOrEqual(2);
  expect(Number(await page.evaluate(() => sessionStorage.getItem("weather:chunk-reload")))).toBeGreaterThan(0);
});
test("leaving a page while its lazy chunk is still loading navigates, never reloads", async ({ page, request }) => {
  const documentAssets = new Set([...(await (await request.get("/weather/history/")).text()).matchAll(/\/assets\/[^"'\s]+\.js/g)].map((match) => match[0]));
  let held = 0, loads = 0;
  // The lazily imported chart chunk never arrives, so it is in flight when the visitor navigates away.
  const chunks = /\/assets\/[^/]+\.js$/;
  await page.route(chunks, (route) => { if (documentAssets.has(new URL(route.request().url()).pathname)) return route.continue(); held += 1; });
  await page.goto("/weather/history/");
  await expect.poll(() => held).toBeGreaterThan(0);
  // The held import stays in flight; the next page's own assets load normally.
  await page.unroute(chunks);
  page.on("load", () => { loads += 1; });
  await page.goto("/about/");
  await expect(page).toHaveURL(/\/about\/$/);
  await page.waitForTimeout(1000);
  expect(loads).toBe(1);
  expect(await page.evaluate(() => sessionStorage.getItem("weather:chunk-reload"))).toBeNull();
});

// Embed card (#45): framing policy per route, cookie-free same-origin loading, no-JS snapshot, accessibility.
const PRIVATE = /"(?:serial|serial_number|hub_sn|device_sn|latitude|longitude|camera_ip|camera_address)"\s*:|\b(?:\d{1,3}\.){3}\d{1,3}\b|\.svc\b|localhost:4174|http:\/\/api\b|API_ORIGIN/i;
function framing(response: APIResponse) {
  const policies = response.headersArray().filter((header) => header.name.toLowerCase() === "content-security-policy").map((header) => header.value);
  expect(policies).toHaveLength(1);
  expect(response.headersArray().filter((header) => /^(?:access-control-|set-cookie$)/i.test(header.name))).toEqual([]);
  return { ancestors: /frame-ancestors ([^;]+)$/.exec(policies[0])?.[1], xfo: response.headers()["x-frame-options"] };
}
test("embed success, 404, 500 and API-down responses allow only vallery.net to frame them; other routes deny", async ({ request }) => {
  const cases: [string, Record<string, unknown>, number, RegExp][] = [
    ["/embed/weather/", {}, 200, /<h2>Temperature<\/h2><strong>32 °F<\/strong>/],
    ["/embed/weather/missing", {}, 404, /Current weather is unavailable right now/],
    ["/embed/weather/", { invalidTime: true }, 500, /Current weather is unavailable right now/],
    ["/embed/weather/", { apiUnavailable: true }, 200, /API unavailable\. Previously received measurements keep aging\./],
  ];
  for (const [path, control, status, body] of cases) {
    await request.post("http://localhost:4174/__control", { data: control });
    const response = await request.get(path);
    expect(response.status()).toBe(status);
    expect(framing(response)).toEqual({ ancestors: "https://vallery.net", xfo: undefined });
    const text = await response.text();
    expect(text).toMatch(body); expect(text).toContain('<a href="https://weather.vallery.net/" target="_top">Open Weather</a>');
    expect(text).not.toMatch(PRIVATE);
  }
  await request.post("http://localhost:4174/__control", { data: {} });
  for (const [path, status] of [["/", 200], ["/weather/history/", 200], ["/editor", 404]] as const) {
    const response = await request.get(path);
    expect(response.status()).toBe(status);
    expect(framing(response)).toEqual({ ancestors: "'none'", xfo: "DENY" });
  }
});
test("a cookie-free browser loads and polls the card without leaving the Weather origin", async ({ browser }) => {
  test.setTimeout(60_000);
  const context = await browser.newContext({ baseURL: "http://localhost:4173" });
  const page = await context.newPage();
  const origins = new Set<string>();
  page.on("request", (request) => origins.add(new URL(request.url()).origin));
  // The chrome-free embed document must hydrate cleanly: React reports mismatches on the console.
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => { if (/hydrat/i.test(message.text())) errors.push(message.text()); });
  await page.goto("/embed/weather/");
  await expect(page.getByRole("heading", { level: 1, name: "Current weather in Longmont" })).toBeVisible();
  // The client's own poll (about 15 s, ±10%) also stays on this origin.
  await page.waitForRequest((request) => new URL(request.url()).pathname === "/api/v1/weather/current", { timeout: 25_000 });
  expect([...origins]).toEqual(["http://localhost:4173"]);
  expect(await context.cookies()).toEqual([]);
  expect(errors).toEqual([]);
  await context.close();
});
test("without JavaScript the card is a dated snapshot that does not claim to update", async ({ browser }) => {
  const context = await browser.newContext({ javaScriptEnabled: false });
  const page = await context.newPage(); await page.goto("http://localhost:4173/embed/weather/");
  await expect(page.getByText(/does not auto-update without JavaScript/)).toBeVisible();
  await expect(page.getByText(/^Snapshot generated/)).toBeVisible();
  await expect(page.locator("main time").first()).toBeVisible();
  await context.close();
});
test("the card passes WCAG 2.2 AA in Chromium at 320 px", async ({ page, browserName }) => {
  // One engine by design (#45); the other projects record this as a no-op pass.
  if (browserName !== "chromium") return;
  await page.setViewportSize({ width: 320, height: 576 });
  await page.goto("/embed/weather/"); await page.evaluate(() => document.fonts.ready);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
  expect(result.violations).toEqual([]);
  expect(await page.evaluate(() => (window as Window & { cspViolations?: string[] }).cspViolations)).toEqual([]);
});

// Linked playback (#40): the sidecar time map links the player, plots, keogram, events and ?t=.
// Fixture segments: 2026-10-31 daytime is [12:30Z, 01:00Z), overnight [01:00Z, 13:30Z); 3,600 frames of 12.5 s.
const EAST_DAY = "/timelapses/skycam_east/2026-10-31/daytime/";
type Linked = { time: number; utc: string; frame: number; paused: boolean };
const linked = (page: Page) => page.evaluate(() => {
  const video = document.querySelector("video")!, readout = document.getElementById("cursor-readout")!;
  return { time: video.currentTime, utc: readout.dataset.utc || "", frame: Number(readout.dataset.frame), paused: video.paused };
});
/** The UTC cursor's frame (from the map) and the player's frame agree, and the player sits inside that frame. */
function agree(state: Linked, start: string, step = 12.5) {
  const frame = Math.floor((Date.parse(state.utc) - Date.parse(start)) / (step * 1000));
  expect(state.frame).toBe(frame);
  expect(Math.abs(state.time - (frame + 0.5) / 60)).toBeLessThanOrEqual(1 / 60);
}
async function ready(page: Page) {
  await expect(page.locator("#cursor-readout")).toHaveAttribute("data-frame", /\d+/);
  await expect(page.getByRole("slider", { name: /Measured weather plots/ })).toBeVisible();
  await expect.poll(() => page.locator("video").evaluate((element: HTMLVideoElement) => element.readyState)).toBeGreaterThanOrEqual(1);
}
test("linked playback: play moves the cursor; chart, keogram and event selections seek within one frame, with no feedback loop", async ({ page }) => {
  await page.goto(EAST_DAY); await ready(page);
  const video = page.locator("video");
  const before = await linked(page);
  expect(before.utc).toBe("2026-10-31T12:30:06.25Z");
  await video.evaluate((element: HTMLVideoElement) => element.play());
  await expect.poll(async () => (await linked(page)).frame, { timeout: 15_000 }).toBeGreaterThan(60);
  await video.evaluate((element: HTMLVideoElement) => element.pause());
  await expect.poll(async () => { const state = await linked(page); return state.frame === Math.floor(state.time * 60 + 1e-6); }).toBe(true);
  // Chart: click 40% across the first panel.
  const over = page.locator(".linked-plot .u-over").first();
  const box = (await over.boundingBox())!;
  await over.click({ position: { x: box.width * 0.4, y: box.height / 2 } });
  await expect.poll(async () => (await linked(page)).utc).not.toBe(before.utc);
  await expect.poll(async () => { const state = await linked(page); return Math.abs(state.time - (state.frame + 0.5) / 60) <= 1 / 60; }).toBe(true);
  const chart = await linked(page); agree(chart, "2026-10-31T12:30:00Z");
  expect(Math.abs(Date.parse(chart.utc) - (Date.parse("2026-10-31T12:30:00Z") + 0.4 * 45_000_000))).toBeLessThan(45_000_000 / box.width * 2);
  // No seek-feedback loop: a paused, seeked player and the cursor stay put.
  await page.waitForTimeout(750);
  expect(await linked(page)).toEqual(chart);
  // Keogram bin: the cursor lands on a bin centre (31.25 s bins).
  const strip = page.locator(".keogram-strip"); const stripBox = (await strip.boundingBox())!;
  await strip.click({ position: { x: stripBox.width * 0.25, y: stripBox.height / 2 } });
  await expect.poll(async () => (Date.parse((await linked(page)).utc) - Date.parse("2026-10-31T12:30:00Z")) % 31_250).toBe(15_625);
  await expect.poll(async () => { const state = await linked(page); return Math.abs(state.time - (state.frame + 0.5) / 60) <= 1 / 60; }).toBe(true);
  agree(await linked(page), "2026-10-31T12:30:00Z");
  // A fixture event through seek(interval): 15:20Z is frame 816.
  await page.getByRole("link", { name: /^Seek video to precipitation onset/ }).click();
  await expect.poll(async () => (await linked(page)).utc).toBe("2026-10-31T15:20:00Z");
  await expect.poll(async () => (await linked(page)).time).toBeCloseTo(816.5 / 60, 2);
  agree(await linked(page), "2026-10-31T12:30:00Z");
  await expect(page).toHaveURL(/\?t=2026-10-31T15%3A20%3A00Z$/);
});
test("plot bounds equal the segment's source interval for a daytime and an overnight recording", async ({ page }) => {
  for (const [path, start, end] of [[EAST_DAY, "2026-10-31T12:30:00Z", "2026-11-01T01:00:00Z"], ["/timelapses/skycam_east/2026-10-31/overnight/", "2026-11-01T01:00:00Z", "2026-11-01T13:30:00Z"]]) {
    const seen: URLSearchParams[] = [];
    page.on("request", (request) => { const url = new URL(request.url()); if (url.pathname === "/api/v1/weather/aggregates") seen.push(url.searchParams); });
    await page.goto(path); await ready(page);
    expect(seen.length).toBe(3);
    for (const query of seen) expect([query.get("start"), query.get("end")]).toEqual([start, end]);
    page.removeAllListeners("request");
  }
});
test("a telemetry gap and a partial recording: gap and coverage shown, nothing interpolated, no time hidden; a missing keogram still plays", async ({ page, request }) => {
  await request.post("http://localhost:4174/__control", { data: { productState: "partial" } });
  await page.goto(EAST_DAY); await ready(page);
  await expect(page.getByText(/^Partial coverage · 95\.0% of the source interval was captured/)).toBeVisible();
  await expect(page.getByText(/^Capture gaps: 2 ranges of slate frames totalling 16 min, shown at their true length/)).toBeVisible();
  await page.getByLabel(/Seek to source time/).fill("2026-10-31T15:10:00Z"); await page.getByLabel(/Seek to source time/).press("Enter");
  await expect(page.locator("#cursor-readout")).toContainText("Temperature mean no observations");
  await expect(page.getByText(/^Temperature mean: .*, 9 without observations; coverage 94\.0%\.$/)).toBeVisible();
  // Inside the 15-minute capture gap the frame is a labelled slate, at its own source time.
  await page.getByLabel(/Seek to source time/).fill("2026-10-31T16:45:00Z"); await page.getByLabel(/Seek to source time/).press("Enter");
  await expect(page.locator("#cursor-readout")).toContainText("Capture gap: this frame is a slate");
  agree(await linked(page), "2026-10-31T12:30:00Z");
  await request.post("http://localhost:4174/__control", { data: {} });
  await page.goto("/timelapses/skycam_west/2026-10-31/overnight/"); await ready(page);
  await expect(page.getByText(/^Keogram unavailable · the recording still plays/)).toBeVisible();
  await expect(page.locator("video")).toHaveCount(1);
});
test("midnight and both DST transitions: repeated local hours carry different offsets and the UTC selection is stable", async ({ page }) => {
  await page.goto("/timelapses/skycam_east/2026-10-31/overnight/"); await ready(page);
  const input = page.getByLabel(/Seek to source time/);
  await input.fill("2026-11-01T07:30:00Z"); await input.press("Enter");
  await expect(page.locator("#cursor-readout")).toContainText("01:30:00 GMT-06:00");
  const first = await linked(page);
  await input.fill("2026-11-01T08:30:00Z"); await input.press("Enter");
  await expect(page.locator("#cursor-readout")).toContainText("01:30:00 GMT-07:00");
  const second = await linked(page);
  expect(second.frame - first.frame).toBe(288);
  await page.reload(); await ready(page);
  await expect.poll(async () => (await linked(page)).utc).toBe("2026-11-01T08:30:00Z");
  await page.goto("/timelapses/skycam_east/2026-03-07/overnight/?t=2026-03-08T09:00:00Z"); await ready(page);
  await expect(page.locator("#cursor-readout")).toContainText("03:00:00 GMT-06:00");
  await expect(page.getByText(/Source interval:/)).toContainText("GMT-07:00");
  agree(await linked(page), "2026-03-08T00:27:00Z", 44_700 / 3600);
});
test("a copied ?t= link restores camera, segment and cursor in a fresh context", async ({ page, browser }) => {
  await page.goto(EAST_DAY); await ready(page);
  await page.getByLabel(/Seek to source time/).fill("2026-10-31T18:00:00.5Z"); await page.getByRole("button", { name: "Seek" }).click();
  await expect.poll(async () => (await linked(page)).utc).toBe("2026-10-31T18:00:00.5Z");
  const href = await page.getByRole("link", { name: "Link to this moment" }).getAttribute("href");
  expect(href).toBe("?t=2026-10-31T18%3A00%3A00.5Z");
  const context = await browser.newContext({ baseURL: "http://localhost:4173" });
  const fresh = await context.newPage();
  await fresh.goto(new URL(href!, `http://localhost:4173${EAST_DAY}`).toString()); await ready(fresh);
  await expect(fresh.getByRole("heading", { level: 1, name: "East sky · Daytime" })).toBeVisible();
  await expect.poll(async () => (await linked(fresh)).utc).toBe("2026-10-31T18:00:00.5Z");
  await expect(fresh.locator("#cursor-readout")).toContainText("12:00:00 GMT-06:00");
  await expect.poll(async () => { const state = await linked(fresh); return Math.abs(state.time - (state.frame + 0.5) / 60) <= 1 / 60; }).toBe(true);
  agree(await linked(fresh), "2026-10-31T12:30:00Z");
  await context.close();
});
test("keyboard-only seek, a text summary, axe on the linked page, one player and bounded points on mobile", async ({ page, request }) => {
  await page.setViewportSize({ width: 320, height: 900 });
  const points: number[] = [];
  page.on("response", async (response) => { if (new URL(response.url()).pathname === "/api/v1/weather/aggregates") points.push(((await response.json()) as { items: unknown[] }).items.length); });
  await page.goto(EAST_DAY); await ready(page);
  const plots = page.getByRole("slider", { name: /Measured weather plots/ });
  await plots.focus(); await page.keyboard.press("Home");
  await expect.poll(async () => (await linked(page)).utc).toBe("2026-10-31T12:32:30Z");
  await page.keyboard.press("ArrowRight");
  await expect.poll(async () => (await linked(page)).utc).toBe("2026-10-31T12:37:30Z");
  await expect(plots).toHaveAttribute("aria-valuetext", /±150 s/);
  const keogram = page.getByRole("slider", { name: /Keogram bin/ });
  await keogram.focus(); await page.keyboard.press("ArrowRight");
  await expect.poll(async () => (await linked(page)).utc).toBe("2026-10-31T12:38:04.375Z");
  await expect(keogram).toHaveAttribute("aria-valuetext", /^Bin 16 of 1440, .*±15\.6 s$/);
  await expect.poll(async () => { const state = await linked(page); return Math.abs(state.time - (state.frame + 0.5) / 60) <= 1 / 60; }).toBe(true);
  await expect(page.locator("#plot-summary li")).toHaveCount(6);
  await page.getByText("Plotted values table").click();
  await expect(page.getByRole("region", { name: "Plotted values table" })).toBeVisible();
  expect((await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze()).violations).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await expect(page.locator("video")).toHaveCount(1);
  // The browser selects one rendition, once: a phone never fetches the web rendition too. Counted at
  // the fixture media origin, since WebKit does not surface media loads as Playwright media requests.
  const requested = (await (await request.get("http://localhost:4174/__media")).json()) as { videos: string[] };
  expect(requested.videos).toEqual([await page.locator("video").evaluate((element: HTMLVideoElement) => new URL(element.currentSrc).pathname)]);
  await expect.poll(() => points.length).toBe(3);
  expect(points.reduce((sum, count) => sum + count, 0)).toBeLessThan(5000);
});
test("the same UTC interval from two cameras shows identical, shared station values", async ({ page }) => {
  const summary = async (path: string) => { await page.goto(path); await ready(page); return page.locator("#plot-summary").innerText(); };
  const east = await summary(EAST_DAY), west = await summary("/timelapses/skycam_west/2026-10-31/daytime/");
  expect(east).toBe(west); expect(east).toContain("Temperature mean:");
  await expect(page.getByRole("heading", { name: "Station measurements · shared by all cameras" })).toBeVisible();
});
test("hourly stills: day and hour navigation, the repeated 01:00 and missing slots, without JavaScript", async ({ browser }) => {
  const context = await browser.newContext({ javaScriptEnabled: false });
  const page = await context.newPage();
  await page.goto("/day/2026-11-01/");
  await expect(page.getByRole("heading", { name: "Hourly stills · calendar day 2026-11-01" })).toBeVisible();
  await page.getByRole("link", { name: "View the day's stills" }).first().click();
  await expect(page).toHaveURL(/\/stills\/\?camera=skycam_east&date=2026-11-01$/);
  // Both 01:00 slots of the fall change, with their own offsets, and an explicit missing slot.
  await expect(page.getByRole("heading", { name: "01:00 UTC-06:00", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "01:00 UTC-07:00 (repeated hour)" })).toBeVisible();
  const cards = page.locator(".still");
  await expect(cards.getByText("Missing · no_eligible_capture")).toBeVisible();
  await expect(cards.getByText(/Off target · nearest capture in the hour/)).toBeVisible();
  await expect(page.getByText(/\+11 min 0\.0 s from :30/)).toBeVisible();
  await page.getByRole("link", { name: "Next day" }).click();
  await expect(page).toHaveURL(/date=2026-11-02/);
  await page.goto("/stills/?camera=skycam_east&date=2026-11-01");
  await page.getByRole("link", { name: /Compare East sky at 01:00 UTC-06:00 across dates/ }).click();
  await expect(page).toHaveURL(/hour=1/);
  // The same clock hour across dates: actual capture times, offsets and the missing day.
  await expect(page.getByRole("heading", { name: "2026-10-31 · 01:00 UTC-06:00" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "2026-10-30 · 01:00 UTC-06:00" })).toBeVisible();
  await expect(page.locator(".still").getByText("Missing · no_capture")).toBeVisible();
  await expect(page.getByText(/\+12\.4 s from :30/)).toBeVisible();
  await expect(page.getByText(/same clock hour is not the same solar phase/)).toBeVisible();
  for (const image of await page.locator(".still img").all()) expect(await image.getAttribute("src")).toMatch(/^\/media\/[A-Za-z0-9_-]+$/);
  await page.getByRole("link", { name: "Next hour" }).click();
  await expect(page).toHaveURL(/hour=2/);
  await context.close();
});
