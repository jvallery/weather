// SPDX-License-Identifier: MIT
/** Inspect every compiled client asset, including lazy routes, without emitting its contents. */
import { lstatSync, readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
if (process.platform !== "linux" || process.env.WEATHER_CI_IN_CLUSTER !== "1") throw new Error("Inspect compiled web output in approved fleet CI");
let files = 0, bytes = 0;
const root = "web/build/client";
function visit(path) {
  const stat = lstatSync(join(root, path));
  if (stat.isSymbolicLink()) throw new Error("Client output must not contain symlinks");
  if (stat.isDirectory()) { for (const name of readdirSync(join(root, path))) visit(join(path, name)); return; }
  if (!stat.isFile() || ++files > 1024 || stat.size > 8 * 1024 * 1024 || (bytes += stat.size) > 64 * 1024 * 1024) throw new Error("Client output exceeds inspection budget");
  if (path.endsWith(".map")) throw new Error("Public source map output is forbidden");
  if (!/\.(js|css|html|json)$/.test(path)) return;
  const text = readFileSync(join(root, path), "utf8");
  if (/http:\/\/api(?::8000)?(?:[\/"'`]|$)|localhost:4174|\.svc\.cluster\.local|API_ORIGIN|DATABASE_URL|CURSOR_HMAC_KEY|sourceMappingURL=|Synthetic test station/.test(text)) throw new Error(`Private or fixture identifier in compiled client asset: ${path}`);
}
visit("");
process.stdout.write(`All ${files} client files (${bytes} bytes) passed private-identifier and source-map checks\n`);
