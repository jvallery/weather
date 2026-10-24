// SPDX-License-Identifier: MIT
import { PassThrough } from "node:stream";
import { createReadableStreamFromReadable } from "@react-router/node";
import { renderToPipeableStream } from "react-dom/server";
import { ServerRouter, type EntryContext } from "react-router";

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
