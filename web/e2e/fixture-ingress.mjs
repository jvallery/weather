// SPDX-License-Identifier: MIT
/** CI-only, fixed local equivalent of the deploy/k8s/ingress.yaml service split: /api/, /media/ and
 * /downloads/ (#20) → api, everything else (including /assets/ and /fonts/) → web. */
import { createServer, request } from "node:http";
if (process.platform !== "linux" || process.env.WEATHER_CI_IN_CLUSTER !== "1") throw new Error("Fixture ingress requires approved in-cluster CI");
createServer((incoming, outgoing) => {
  const api = /^\/(?:api|media|downloads)\//.test(incoming.url || "");
  const upstream = request({ hostname: "localhost", port: api ? 4174 : 4175, path: incoming.url, method: incoming.method, headers: incoming.headers, timeout: 10_000 }, (response) => {
    outgoing.writeHead(response.statusCode || 503, response.headers); response.pipe(outgoing);
  });
  upstream.on("error", () => { if (!outgoing.headersSent) outgoing.writeHead(503); outgoing.end("Synthetic test upstream unavailable"); });
  upstream.on("timeout", () => upstream.destroy());
  incoming.on("aborted", () => upstream.destroy());
  incoming.pipe(upstream);
}).listen(4173);
