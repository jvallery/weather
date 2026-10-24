// SPDX-License-Identifier: MIT
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { expect, test } from "vitest";

test("public schema construction and parsing never probe dynamic code under strict CSP", () => {
  const program = `
    import { pathToFileURL } from "node:url";
    import { readFileSync } from "node:fs";
    let calls = 0;
    globalThis.Function = new Proxy(globalThis.Function, {
      apply() { calls++; throw new Error("Dynamic compiler blocked"); },
      construct() { calls++; throw new Error("Dynamic compiler blocked"); }
    });
    const { currentSchema } = await import(pathToFileURL(process.argv[1]));
    currentSchema.parse(JSON.parse(readFileSync(process.argv[2], "utf8")));
    process.stdout.write(JSON.stringify({ calls }));
  `;
  const result = spawnSync(process.execPath, ["--experimental-strip-types", "--input-type=module", "-e", program,
    fileURLToPath(new URL("./contracts.ts", import.meta.url)), fileURLToPath(new URL("../fixtures/current.json", import.meta.url))], { encoding: "utf8", timeout: 5000 });
  expect(result.status).toBe(0);
  expect(JSON.parse(result.stdout)).toEqual({ calls: 0 });
});
