// SPDX-License-Identifier: MIT
/** Preserve one predecessor's compiled assets without accumulating its own ancestors. */
import { createHash } from "node:crypto";
import { copyFileSync, existsSync, lstatSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
const inventoryName = ".weather-assets.json";
const maxFiles = 512, maxBytes = 64 * 1024 * 1024, maxFileBytes = 8 * 1024 * 1024;
function identity(root, path) {
  if (path.length > 512 || !/^assets\/[A-Za-z0-9_./-]+$/.test(path) || path.split("/").some((part) => ["", ".", ".."].includes(part))) throw new Error("Invalid asset path");
  let parent = root;
  for (const part of path.split("/").slice(0, -1)) {
    parent = join(parent, part);
    if (!lstatSync(parent).isDirectory() || lstatSync(parent).isSymbolicLink()) throw new Error("Invalid asset directory");
  }
  const source = join(root, path);
  const stat = lstatSync(source);
  if (!stat.isFile() || stat.size > maxFileBytes) throw new Error("Invalid asset file or size");
  const bytes = readFileSync(source);
  return { path, bytes: bytes.length, sha256: createHash("sha256").update(bytes).digest("hex") };
}
function budget(rows) {
  if (rows.length > maxFiles || rows.reduce((sum, row) => sum + row.bytes, 0) > maxBytes) throw new Error("Asset inventory exceeds budget");
}
function scan(root) {
  const rows = [];
  let visited = 0;
  function visit(path) {
    if (++visited > maxFiles * 2) throw new Error("Asset tree exceeds budget");
    const stat = lstatSync(join(root, path));
    if (stat.isSymbolicLink()) throw new Error("Asset symlinks are forbidden");
    if (stat.isDirectory()) for (const name of readdirSync(join(root, path)).sort()) visit(`${path}/${name}`);
    else { rows.push(identity(root, path)); budget(rows); }
  }
  visit("assets"); return rows;
}
function previousInventory(root, allowLegacyBaseline) {
  const file = join(root, inventoryName);
  if (!existsSync(file)) {
    if (!allowLegacyBaseline) throw new Error("Previous image lacks its current-build inventory");
    return scan(root);
  }
  if (!lstatSync(file).isFile() || lstatSync(file).size > 256 * 1024) throw new Error("Invalid previous inventory file");
  const value = JSON.parse(readFileSync(file, "utf8"));
  if (value.schema !== 1 || !Array.isArray(value.assets) || value.assets.length > maxFiles) throw new Error("Invalid previous inventory");
  const paths = new Set();
  let totalBytes = 0;
  const rows = value.assets.map((row) => {
    if (!row || typeof row.path !== "string" || !Number.isSafeInteger(row.bytes) || row.bytes < 0 || row.bytes > maxFileBytes || !/^[a-f0-9]{64}$/.test(row.sha256)) throw new Error("Invalid asset identity");
    totalBytes += row.bytes; if (totalBytes > maxBytes) throw new Error("Asset inventory exceeds budget");
    if (paths.has(row.path)) throw new Error("Duplicate asset identity"); paths.add(row.path);
    const actual = identity(root, row.path);
    if (actual.bytes !== row.bytes || actual.sha256 !== row.sha256) throw new Error("Previous asset bytes differ from inventory");
    return actual;
  });
  budget(rows); return rows;
}
export function retainWebAssets(currentRoot, previousRoot, allowLegacyBaseline = false) {
  const current = scan(currentRoot);
  const previous = previousInventory(previousRoot, allowLegacyBaseline);
  const byPath = new Map(current.map((row) => [row.path, row]));
  budget([...current, ...previous.filter((row) => !byPath.has(row.path))]);
  // Check every collision before copying anything. A reused name must have identical bytes.
  for (const row of previous) {
    const compiled = byPath.get(row.path);
    if (compiled && (compiled.sha256 !== row.sha256 || compiled.bytes !== row.bytes)) throw new Error("Asset name collision with different content");
  }
  for (const row of previous) if (!byPath.has(row.path)) {
    const destination = join(currentRoot, row.path); mkdirSync(dirname(destination), { recursive: true });
    copyFileSync(join(previousRoot, row.path), destination);
  }
  writeFileSync(join(currentRoot, inventoryName), `${JSON.stringify({ schema: 1, assets: current })}\n`);
  return { current: current.length, retained: previous.length };
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  if (![4, 5].includes(process.argv.length) || (process.argv[4] && process.argv[4] !== "--allow-legacy-baseline")) throw new Error("Expected current and previous client directories");
  process.stdout.write(`${JSON.stringify(retainWebAssets(process.argv[2], process.argv[3], process.argv[4] === "--allow-legacy-baseline"))}\n`);
}
