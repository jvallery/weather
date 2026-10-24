// SPDX-License-Identifier: MIT
import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
const paths = ["/", "/about/", "/archive/?month=2026-10", "/archive/2026/10/", "/day/2026-10-31/", "/timelapses/skycam_east/2026-10-31/daytime/", "/weather/history/", "/search/"];
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
  for (const state of ["partial", "unavailable", "expired"]) {
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
  await expect(page.getByRole("link", { name: /Export this bounded observation page/ })).toHaveCount(0);
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
