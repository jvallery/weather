// SPDX-License-Identifier: MIT
import { afterEach, expect, test, vi } from "vitest";
import { Counter, register } from "prom-client";
import { handleError } from "./entry.server";
afterEach(() => { register.removeSingleMetric("weather_web_ssr_failures_total"); vi.restoreAllMocks(); });
test("framework failures expose only bounded metrics, never URLs or stack diagnostics", async () => {
  const log = vi.spyOn(console, "error").mockImplementation(() => {});
  const counter = new Counter({ name: "weather_web_ssr_failures_total", help: "Synthetic test", labelNames: ["reason"] });
  const request = new Request("https://weather.invalid/unrecognized?synthetic=private");
  handleError(new Error("Synthetic private diagnostic"), { request });
  handleError({ status: 404, statusText: "Not Found", internal: false, data: "Synthetic private path" }, { request });
  const abort = new AbortController(); abort.abort();
  handleError(new Error("Synthetic aborted request"), { request: new Request(request, { signal: abort.signal }) });
  expect((await counter.get()).values).toEqual([{ value: 1, labels: { reason: "render_error" } }]);
  expect(log).not.toHaveBeenCalled();
});
