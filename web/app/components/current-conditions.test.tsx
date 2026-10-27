// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import fixture from "../fixtures/current.json";
import { parseCurrent } from "../lib/weather-api";
import { CurrentConditions } from "./current-conditions";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

test("Today before the API serves data: says API unavailable and shows no fabricated values (#21)", () => {
  vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new Error("offline"))));
  const empty = parseCurrent({ ...fixture, data_state: "unavailable", metrics: [] })!;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const { container } = render(<QueryClientProvider client={client}>
    <CurrentConditions initial={empty} renderedAt={Date.now()} units="metric" apiUnavailable /></QueryClientProvider>);
  expect(screen.getByText(/^API unavailable\./)).toBeTruthy();
  expect(screen.getByText("No current measurements were returned. No substitute values are displayed.")).toBeTruthy();
  expect(container.querySelectorAll(".metric")).toHaveLength(0);
  expect(container.textContent).not.toMatch(/\d+(\.\d+)?\s?(°|%|hPa|m\/s|mm|km\/h|mph)/);
});
