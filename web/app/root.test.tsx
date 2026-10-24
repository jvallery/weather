// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
import { cleanup, render, screen } from "@testing-library/react";
import { createRoutesStub, Outlet, UNSAFE_ErrorResponseImpl } from "react-router";
import { afterEach, expect, test, vi } from "vitest";
import { ErrorBoundary, headers } from "./root";
import { busy } from "./lib/public-api.server";
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

async function boundaryFor(thrown: unknown) {
  // The boundary renders <html>; React reports the jsdom container nesting, which is not under test.
  vi.spyOn(console, "error").mockImplementation(() => {});
  const Stub = createRoutesStub([{ path: "/", loader: () => { throw thrown; }, Component: () => null, ErrorBoundary }]);
  render(<Stub initialEntries={["/"]} />);
  return screen.findByRole("heading", { level: 1 });
}
test("loader overload renders the busy state, not API unavailable", async () => {
  expect((await boundaryFor(busy())).textContent).toBe("Weather is busy");
  expect(screen.getByText(/Please retry in a few seconds/)).toBeTruthy();
});
test("a client navigation's plain 503 busy .data response renders the busy state", async () => {
  // single-fetch builds exactly this from server.mjs's non-React-Router 503 body.
  expect((await boundaryFor(new UNSAFE_ErrorResponseImpl(503, "Service Unavailable", "busy"))).textContent).toBe("Weather is busy");
});
test("other 503s stay the generic unavailable state", async () => {
  expect((await boundaryFor(new UNSAFE_ErrorResponseImpl(503, "Service Unavailable", "Weather is temporarily unavailable."))).textContent).toBe("Weather is temporarily unavailable");
  cleanup();
  expect((await boundaryFor(new Error("render failed"))).textContent).toBe("Weather is temporarily unavailable");
});
test("the busy Retry-After reaches the document response", () => {
  const args = (errorHeaders?: Headers) => ({ errorHeaders }) as Parameters<typeof headers>[0];
  expect(new Headers(headers(args(new Headers({ "Retry-After": "5" })))).get("Retry-After")).toBe("5");
  expect(new Headers(headers(args())).get("Retry-After")).toBeNull();
});
test("embed route errors render the unavailable card, not the site error page", async () => {
  vi.spyOn(console, "error").mockImplementation(() => {});
  const Stub = createRoutesStub([{ path: "/", Component: Outlet, ErrorBoundary, children: [
    { path: "embed/weather", loader: () => { throw new Error("Synthetic render failure"); }, Component: () => null },
  ] }]);
  for (const path of ["/embed/weather/", "/embed/weather/missing"]) {
    render(<Stub initialEntries={[path]} />);
    expect((await screen.findByRole("heading", { level: 1 })).textContent).toBe("Current weather in Longmont");
    expect(screen.getByText(/^Current weather is unavailable right now\./)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Open Weather" }).getAttribute("target")).toBe("_top");
    expect(screen.queryByText(/Return to Today/)).toBeNull();
    cleanup();
  }
});
