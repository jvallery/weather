// SPDX-License-Identifier: MIT
import { useRef, useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Links, Meta, NavLink, Outlet, Scripts, ScrollRestoration, isRouteErrorResponse, useLoaderData, useLocation, useRouteError, type HeadersFunction, type LoaderFunctionArgs } from "react-router";
import { EmbedUnavailableCard } from "./components/embed-weather-card";
import { SiteHeader } from "./components/site-header";
import { isEmbedPath } from "../security-headers.mjs";
import styles from "./styles.css?url";
export const links = () => [{ rel: "stylesheet", href: styles }];
export const headers: HeadersFunction = ({ errorHeaders }) => {
  const headers = new Headers();
  const retry = errorHeaders?.get("Retry-After");
  if (retry) headers.set("Retry-After", retry);
  return headers;
};
export function loader({ request }: LoaderFunctionArgs) {
  return { nonce: request.headers.get("x-weather-csp-nonce") || "" };
}
export default function Root() {
  const loaded = useLoaderData<typeof loader>();
  const nonce = useRef(loaded.nonce).current;
  // Each SSR render/request owns its cache. No process-global public or private query state.
  const [client] = useState(() => new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false, refetchOnReconnect: false } } }));
  const embed = isEmbedPath(useLocation().pathname);
  // The framed card has no site chrome; its only link leaves the frame (D13).
  if (embed) return <html lang="en"><head><meta charSet="utf-8" /><meta name="viewport" content="width=device-width, initial-scale=1" /><Meta /><Links /></head><body><QueryClientProvider client={client}><Outlet /></QueryClientProvider><Scripts nonce={nonce} /></body></html>;
  return <html lang="en"><head><meta charSet="utf-8" /><meta name="viewport" content="width=device-width, initial-scale=1" /><Meta /><Links /></head><body><a href="#main-content" className="skip-link">Skip to content</a>
    <SiteHeader />
    <main id="main-content" className="shell" tabIndex={-1}><QueryClientProvider client={client}><Outlet /></QueryClientProvider></main>
    <footer className="site-footer"><div className="shell"><span>Weather · observations from one backyard station and three cameras</span><NavLink to="/about/">About and methods</NavLink><NavLink to="/stills/">Hourly stills</NavLink><a href="https://vallery.net/">vallery.net</a><p className="small"><a href="/fonts/OFL-sans.txt">IBM Plex Sans license</a> · <a href="/fonts/OFL-mono.txt">IBM Plex Mono license</a></p></div></footer>
    <ScrollRestoration nonce={nonce} /><Scripts nonce={nonce} /></body></html>;
}
export function ErrorBoundary() {
  const error = useRouteError();
  const embed = isEmbedPath(useLocation().pathname);
  // Embed errors stay a renderable card inside the parent frame, under the same framing policy.
  if (embed) return <html lang="en"><head><meta charSet="utf-8" /><meta name="viewport" content="width=device-width, initial-scale=1" /><title>Current weather unavailable</title><Links /></head><body><EmbedUnavailableCard /></body></html>;
  const status = isRouteErrorResponse(error) ? error.status : 503;
  const busy = isRouteErrorResponse(error) && error.status === 503 && error.data === "busy";
  return <html lang="en"><head><meta charSet="utf-8" /><meta name="viewport" content="width=device-width, initial-scale=1" /><title>{busy ? "Weather is busy" : "Weather unavailable"}</title><Links /></head><body><main className="shell error-page"><span className="eyebrow">Weather</span><h1>{busy ? "Weather is busy" : status === 404 ? "Page not found" : status === 400 ? "Check the requested dates or filters" : "Weather is temporarily unavailable"}</h1><p>{busy ? "Too many pages are loading at once. Please retry in a few seconds." : "The requested page could not be loaded. No internal service details are included here."}</p><p><a className="button-link" href="/">Return to Now</a></p></main></body></html>;
}
