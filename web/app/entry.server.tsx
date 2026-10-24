// SPDX-License-Identifier: MIT
import { PassThrough } from "node:stream";
import { Counter, register } from "prom-client";
import { createReadableStreamFromReadable } from "@react-router/node";
import { renderToPipeableStream } from "react-dom/server";
import { ServerRouter, isRouteErrorResponse, type EntryContext } from "react-router";

export default function handleRequest(request: Request, status: number, headers: Headers, routerContext: EntryContext): Promise<Response> {
  return new Promise((resolve, reject) => {
    const nonce = request.headers.get("x-weather-csp-nonce") || "";
    let shellReady = false;
    const stream = renderToPipeableStream(<ServerRouter context={routerContext} url={request.url} nonce={nonce} />, {
      nonce,
      onShellReady() {
        shellReady = true;
        const body = new PassThrough();
        body.on("close", () => clearTimeout(timeout));
        const readable = createReadableStreamFromReadable(body);
        headers.set("Content-Type", "text/html; charset=utf-8");
        resolve(new Response(readable, { status, headers }));
        stream.pipe(body);
      },
      onShellError() { reject(new Error("Page rendering unavailable")); },
      onError() { if (!shellReady) status = 503; },
    });
    const timeout = setTimeout(() => stream.abort(), 10_000);
    timeout.unref();
    request.signal.addEventListener("abort", () => { clearTimeout(timeout); stream.abort(); }, { once: true });
  });
}

/** Framework errors use bounded metrics; request URLs and stack text are never logged. */
export function handleError(error: unknown, { request }: { request: Request }): void {
  // Overload is already counted as an "overloaded" API admission result.
  const busy = typeof error === "object" && error !== null && "data" in error && error.data === "busy";
  if (request.signal.aborted || busy || (isRouteErrorResponse(error) && error.status < 500)) return;
  const counter = register.getSingleMetric("weather_web_ssr_failures_total");
  if (counter instanceof Counter) counter.labels("render_error").inc();
}
