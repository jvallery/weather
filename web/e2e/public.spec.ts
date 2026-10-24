// SPDX-License-Identifier: MIT
import { test, expect, type APIResponse, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
// Production routes only /api/ to the api (deploy/k8s/ingress.yaml); /media/ and /downloads/ arrive with #20.
// Until then the page under test receives the synthetic media directly from the fixture API.
async function syntheticMedia(page: Page) {
  await page.route(/^http:\/\/localhost:4173\/(?:media|downloads)\//, async (route) => route.fulfill({ response: await route.fetch({ url: `http://localhost:4174${new URL(route.request().url()).pathname}` }) }));
}
const paths = ["/", "/about/", "/archive/?month=2026-10", "/archive/2026/10/", "/day/2026-10-31/", "/timelapses/skycam_east/2026-10-31/daytime/", "/weather/history/", "/weather/almanac/", "/search/"];
test.beforeEach(async ({ request, page }) => {
  await syntheticMedia(page);
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
  page.on("request", (request) => { if (!new URL(request.url()).hostname.includes("localhost")) unexpected.push(request.url()); });
  await page.emulateMedia({ reducedMotion: "reduce" }); await page.goto("/");
  await expect(page.locator(".preview-placeholder")).toHaveCount(3);
  await expect(page.getByText("Daytime latest video · coming soon", { exact: true })).toHaveCount(3);
  await expect(page.getByText("Overnight latest video · coming soon", { exact: true })).toHaveCount(3);
  expect(await page.evaluate(() => document.getAnimations().length)).toBe(0);
  await page.goto("/about/");
  for (const topic of [/Tempest/, /east sky/, /west sky/, /front yard/, /6°/, /7,200/, /not measurements/, /private notes/, /no forecast/]) await expect(page.getByText(topic).first()).toBeVisible();
  await page.goto("/weather/history/"); await expect(page.getByText(/GMT-06:00/).first()).toBeVisible(); await expect(page.getByText(/GMT-07:00/).first()).toBeVisible();
  expect(unexpected).toEqual([]);
});
test("safe interpretation text, lifecycle states and quality preservation", async ({ page, request }) => {
  await request.post("http://localhost:4174/__control", { data: { description: "<script>window.SYNTHETIC_XSS=1</script>" } });
  await page.goto("/timelapses/skycam_east/2026-10-31/daytime/");
  expect(await page.evaluate(() => (window as Window & { SYNTHETIC_XSS?: number }).SYNTHETIC_XSS)).toBeUndefined();
  await expect(page.getByText("<script>window.SYNTHETIC_XSS=1</script>")).toBeVisible();
  const video = page.locator("video"); expect(await video.getAttribute("autoplay")).toBeNull();
  await expect.poll(() => video.evaluate((element: HTMLVideoElement) => element.readyState)).toBeGreaterThanOrEqual(1);
  expect(await video.evaluate((element: HTMLVideoElement) => element.paused)).toBe(true);
  await video.evaluate((element: HTMLVideoElement) => { element.currentTime = 12; });
  await page.getByLabel("Playback quality").selectOption("mobile");
  await expect(video).toHaveAttribute("src", /mobile$/);
  await expect.poll(() => video.evaluate((element: HTMLVideoElement) => element.currentTime)).toBeCloseTo(12, 0);
  // A partial-coverage recording stays playable and says so (C6).
  await request.post("http://localhost:4174/__control", { data: { productState: "partial" } });
  await page.reload(); await expect(page.locator("video")).toHaveCount(1);
  await expect(page.getByText(/^Partial coverage · 95\.0% of the source interval was captured/)).toBeVisible();
  await expect(page.getByText(/^Partial/).first()).toBeVisible();
  await expect.poll(() => video.evaluate((element: HTMLVideoElement) => element.readyState)).toBeGreaterThanOrEqual(1);
  for (const state of ["unavailable", "expired"]) {
    await request.post("http://localhost:4174/__control", { data: { productState: state } });
    await page.reload(); await expect(page.locator("video, main img")).toHaveCount(0);
    await expect(page.getByText(new RegExp(`^${state[0].toUpperCase() + state.slice(1)}`)).first()).toBeVisible();
  }
});
test("concurrent SSR requests have isolated query state and expose no private upstream", async ({ request }) => {
  await request.post("http://localhost:4174/__control", { data: { unique: true } });
  const bodies = await Promise.all(Array.from({ length: 4 }, async () => (await request.get("http://localhost:4173/")).text()));
  const rendered = bodies.map((body) => /<h3>Temperature<\/h3><strong>([^<]+)<\/strong>/.exec(body)?.[1]);
  expect(rendered.every(Boolean)).toBe(true); expect(new Set(rendered).size).toBe(4);
  expect(bodies.every((body) => !body.includes("http://api") && !body.includes("localhost:4174"))).toBe(true);
});

test("About is complete without JavaScript and font notices match approved bytes", async ({ browser, request }) => {
  const context = await browser.newContext({ javaScriptEnabled: false });
  const page = await context.newPage(); await page.goto("http://localhost:4173/about/");
  for (const topic of [/Tempest/, /east sky/, /west sky/, /front yard/, /Measured values/, /Derived values/, /AI descriptions/, /Human-edited/, /6°/, /7,200/, /not measurements/, /private notes/, /at least 10 minutes/, /no forecast/, /person identification/]) await expect(page.getByText(topic).first()).toBeVisible();
  await context.close();
  const provenance = JSON.parse(readFileSync(new URL("../../docs/research/font-provenance.json", import.meta.url), "utf8"));
  for (const font of provenance.packages) {
    for (const [name, digest] of [[font.font_member.split("/").at(-1), font.font_sha256], [font.name.includes("sans") ? "OFL-sans.txt" : "OFL-mono.txt", font.license_sha256]]) {
      const response = await request.get(`/fonts/${name}`); expect(response.ok()).toBe(true);
      expect(createHash("sha256").update(await response.body()).digest("hex")).toBe(digest);
    }
  }
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
  expect(await manifest.json()).toMatchObject({ "routes/not-ready": { path: "search" } });
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
