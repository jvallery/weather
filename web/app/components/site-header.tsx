// SPDX-License-Identifier: MIT
import { Link, useLocation } from "react-router";

/** Sections and the paths each one owns: a recording or a day belongs to Timelapses. */
const SECTIONS = [
  { label: "Now", to: "/", owns: (path: string) => path === "/" },
  { label: "Timelapses", to: "/archive/", owns: (path: string) => /^\/(archive|day|timelapses|stills)\//.test(path) },
  { label: "History", to: "/weather/history/", owns: (path: string) => path.startsWith("/weather/history/") },
  { label: "Almanac", to: "/weather/almanac/", owns: (path: string) => path.startsWith("/weather/almanac/") },
  { label: "Events", to: "/events/", owns: (path: string) => path.startsWith("/events/") },
  { label: "Search", to: "/search/", owns: (path: string) => path.startsWith("/search/") },
] as const;

/** vallery.net's V wordmark, with the mint dot. */
export function Wordmark() {
  return <svg viewBox="0 0 42 42" aria-hidden="true" focusable="false"><path className="brand-mark" d="M4 6h8l9 25L30 6h8L24.4 37h-7.1L4 6Z" /><circle className="brand-dot" cx="34" cy="32" r="4" /></svg>;
}

/** The same URL with only `units` changed: a plain link, so it works without JavaScript. */
export function unitsHref(pathname: string, search: string, units: "us" | "metric"): string {
  const query = new URLSearchParams(search);
  if (units === "metric") query.set("units", "metric"); else query.delete("units");
  return `${pathname}${query.size ? `?${query}` : ""}`;
}

export function SiteHeader() {
  const { pathname, search } = useLocation();
  const metric = new URLSearchParams(search).get("units") === "metric";
  // The unit choice travels with section links, so a metric reader stays metric.
  const carry = metric ? "?units=metric" : "";
  return <header className="site-header"><div className="shell">
    <Link className="brand" to={`/${carry}`} aria-label="Weather, home"><Wordmark /><span>Weather<small> · vallery.net</small></span></Link>
    <nav className="primary-nav" aria-label="Primary">{SECTIONS.map((section) => <Link key={section.to} to={`${section.to}${carry}`} aria-current={section.owns(pathname) ? "page" : undefined}>{section.label}</Link>)}</nav>
    <div className="header-tools">
      <nav className="units" aria-label="Units"><Link to={unitsHref(pathname, search, "us")} aria-current={!metric} aria-label="US customary units (°F)">°F</Link><Link to={unitsHref(pathname, search, "metric")} aria-current={metric} aria-label="Metric units (°C)">°C</Link></nav>
      <a href="https://vallery.net/">vallery.net ↗</a>
    </div>
  </div></header>;
}
