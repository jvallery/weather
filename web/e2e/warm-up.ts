// SPDX-License-Identifier: MIT
// Playwright globalSetup (weather#209). In CI each engine runs in its own job, so
// webkit and firefox no longer start after chromium has warmed the fixture stack.
// Render every route the suites visit once through the fixture ingress, so the
// first test in each engine does not pay the cold server-render and fixture cost
// inside its per-test timeout. Requests carry no Cf-Connecting-Ip, so the rate
// limiter does not count them, and the fixture control state is reset afterwards.
const ingress = "http://localhost:4173";
const routes = ["/", "/about/", "/archive/?month=2026-10", "/archive/2026/10/", "/day/2026-10-31/", "/timelapses/skycam_east/2026-10-31/daytime/", "/timelapses/skycam_west/2026-10-31/overnight/", "/weather/history/", "/weather/almanac/", "/events/", "/embed/weather/", "/search/"];

export default async function warmUp(): Promise<void> {
  const started = Date.now();
  for (const route of routes) {
    try {
      const response = await fetch(`${ingress}${route}`, { signal: AbortSignal.timeout(60_000) });
      await response.arrayBuffer();
    } catch (error) {
      // Warm-up is best effort: the tests themselves assert every route.
      console.warn(`warm-up ${route}: ${String(error)}`);
    }
  }
  await fetch("http://localhost:4174/__control", { method: "POST", headers: { "content-type": "application/json" }, body: "{}", signal: AbortSignal.timeout(30_000) });
  console.log(`warm-up: ${routes.length} routes in ${Date.now() - started} ms`);
}
