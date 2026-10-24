// SPDX-License-Identifier: MIT
import { expect, test } from "vitest";
import { securityHeaders } from "../security-headers.mjs";

const policy = (path: string) => securityHeaders(path, "synthetic-nonce") as Record<string, string>;
const ancestors = (path: string) => /frame-ancestors ([^;]+)$/.exec(policy(path)["Content-Security-Policy"])?.[1];
// server.mjs sets these before any handler, so a route's 200, 404, 500 and API-down responses all carry them.
test("only the embed route and its error paths may be framed, and only by https://vallery.net", () => {
  for (const path of ["/embed/weather/", "/embed/weather", "/embed/weather/missing", "/EMBED/Weather/"]) {
    expect(ancestors(path)).toBe("https://vallery.net");
    expect(policy(path)).not.toHaveProperty("X-Frame-Options");
  }
  for (const path of ["/", "/weather/history/", "/editor", "/about/", "/embed/", "/embed/weatherx", "/api/v1/weather/current", "/x/embed/weather/"]) {
    expect(ancestors(path)).toBe("'none'");
    expect(policy(path)["X-Frame-Options"]).toBe("DENY");
  }
});
test("the embed keeps every other protection and adds no CORS", () => {
  const embed = policy("/embed/weather/"), page = policy("/");
  expect(embed["Content-Security-Policy"].replace("https://vallery.net", "'none'")).toBe(page["Content-Security-Policy"]);
  expect(embed["Content-Security-Policy"]).toContain("script-src 'self' 'nonce-synthetic-nonce'");
  expect(embed["Content-Security-Policy"]).not.toMatch(/unsafe|\*|www\.|localhost/);
  for (const headers of [embed, page]) expect(Object.keys(headers).filter((name) => /^access-control-/i.test(name))).toEqual([]);
  const { "X-Frame-Options": deny, ...unframed } = page;
  expect(deny).toBe("DENY");
  expect({ ...embed, "Content-Security-Policy": "" }).toEqual({ ...unframed, "Content-Security-Policy": "" });
});
