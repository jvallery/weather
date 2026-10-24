// SPDX-License-Identifier: MIT
import { useRef, useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Links, Meta, NavLink, Outlet, Scripts, ScrollRestoration, isRouteErrorResponse, useLoaderData, useRouteError, type LoaderFunctionArgs } from "react-router";
import styles from "./styles.css?url";
export const links = () => [{ rel: "stylesheet", href: styles }];
export function loader({ request }: LoaderFunctionArgs) {
  return { nonce: request.headers.get("x-weather-csp-nonce") || "" };
}
export default function Root() {
  const loaded = useLoaderData<typeof loader>();
  const nonce = useRef(loaded.nonce).current;
  // Each SSR render/request owns its cache. No process-global public or private query state.
  const [client] = useState(() => new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false, refetchOnReconnect: false } } }));
  return <html lang="en"><head><meta charSet="utf-8" /><meta name="viewport" content="width=device-width, initial-scale=1" /><Meta /><Links /></head><body><a href="#main-content" className="skip-link">Skip to content</a><div className="shell"><header className="site-header"><div className="brand-block"><NavLink className="brand" to="/">Weather</NavLink><a href="https://vallery.net/">vallery.net</a></div><nav aria-label="Primary"><NavLink end to="/">Today</NavLink><NavLink to="/archive/">Archive</NavLink><NavLink to="/weather/history/">Weather history</NavLink><NavLink to="/search/">Search</NavLink><NavLink to="/about/">About</NavLink></nav></header><main id="main-content" tabIndex={-1}><QueryClientProvider client={client}><Outlet /></QueryClientProvider></main><footer className="site-footer">Weather · local observations · source times and uncertainty stay visible · <NavLink to="/about/">Methods and limitations</NavLink><p><a href="/fonts/OFL-sans.txt">IBM Plex Sans license</a> · <a href="/fonts/OFL-mono.txt">IBM Plex Mono license</a></p></footer></div><ScrollRestoration nonce={nonce} /><Scripts nonce={nonce} /></body></html>;
}
export function ErrorBoundary() {
  const error = useRouteError();
  const status = isRouteErrorResponse(error) ? error.status : 503;
  return <html lang="en"><head><meta charSet="utf-8" /><meta name="viewport" content="width=device-width, initial-scale=1" /><title>Weather unavailable</title><Links /></head><body><main className="shell"><h1>{status === 404 ? "Page not found" : status === 400 ? "Check the requested dates or filters" : "Weather is temporarily unavailable"}</h1><p>The requested page could not be loaded. No internal service details are included here.</p><a href="/">Return to Today</a></main></body></html>;
}
