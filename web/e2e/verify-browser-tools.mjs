// SPDX-License-Identifier: MIT
import { accessSync, constants } from "node:fs";
import { createRequire } from "node:module";
import { chromium, firefox, webkit } from "@playwright/test";
if (process.platform !== "linux" || process.env.WEATHER_CI_IN_CLUSTER !== "1") throw new Error("Browser checks require approved in-cluster CI");
if (process.env.PLAYWRIGHT_BROWSERS_PATH !== "/opt/ms-playwright") throw new Error("Use the qualified Weather CI browser closure");
const require = createRequire(import.meta.url);
if (require("@playwright/test/package.json").version !== "1.64.1") throw new Error("Browser package differs from the qualified lock");
for (const engine of [chromium, webkit, firefox]) {
  const executable = engine.executablePath();
  if (!executable.startsWith("/opt/ms-playwright/")) throw new Error("Browser executable is outside the qualified image closure");
  accessSync(executable, constants.R_OK | constants.X_OK);
}
process.stdout.write("Qualified Playwright 1.64.1 executable paths are readable by the non-root CI identity\n");
