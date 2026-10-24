// SPDX-License-Identifier: MIT
/** Consume local code-first JSON only; generation never resolves remote schemas. */
import openapiTS, { astToString } from "openapi-typescript";
let json = "";
for await (const chunk of process.stdin) {
  json += chunk;
  if (Buffer.byteLength(json) > 2 * 1024 * 1024) throw new Error("Contract exceeds generation budget");
}
const document = JSON.parse(json);
function localReferences(value) {
  if (!value || typeof value !== "object") return;
  if ("$ref" in value && (typeof value.$ref !== "string" || !value.$ref.startsWith("#/"))) throw new Error("Remote contract references are forbidden");
  for (const child of Object.values(value)) localReferences(child);
}
localReferences(document);
const ast = await openapiTS(document, { alphabetize: true, immutable: true, silent: true });
process.stdout.write("// SPDX-License-Identifier: MIT\n// Generated from code-first Pydantic OpenAPI; run scripts/ci.sh contracts --write.\n" + astToString(ast));
