// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { createMemoryRouter, RouterProvider, useLoaderData } from "react-router";
import { UnitForm } from "./common";
afterEach(cleanup);
test("display units follow URL navigation after the select has been edited", async () => {
  function Page() { return <UnitForm units={useLoaderData() as string} />; }
  const router = createMemoryRouter([{ path: "/", loader: ({ request }) => new URL(request.url).searchParams.get("units") || "us", Component: Page }], { initialEntries: ["/?units=us"] });
  try {
    await act(async () => { render(<RouterProvider router={router} />); });
    const select = await screen.findByLabelText("Display units");
    fireEvent.change(select, { target: { value: "metric" } });
    await act(async () => { await router.navigate("/?units=metric"); });
    expect((screen.getByLabelText("Display units") as HTMLSelectElement).value).toBe("metric");
    await act(async () => { await router.navigate("/?units=us"); });
    expect((screen.getByLabelText("Display units") as HTMLSelectElement).value).toBe("us");
  } finally { router.dispose(); }
});
