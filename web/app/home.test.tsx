// SPDX-License-Identifier: MIT
import { expect, test } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import Home from "./routes/home";

test("renders the commissioning page", () => {
  expect(renderToStaticMarkup(<Home />)).toContain("The weather archive is being commissioned.");
});
