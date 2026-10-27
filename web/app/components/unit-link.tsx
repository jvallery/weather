// SPDX-License-Identifier: MIT
import { Link as RouterLink, useLocation, type LinkProps } from "react-router";

/** `to` with `units=metric` added, unless it already chooses units or leaves the site. */
export function withMetric(to: string): string {
  if (/^[a-z]+:/i.test(to) || to.startsWith("//") || to.startsWith("#")) return to;
  const [target, hash] = to.split("#", 2);
  const at = target.indexOf("?");
  const path = at < 0 ? target : target.slice(0, at);
  const query = new URLSearchParams(at < 0 ? "" : target.slice(at + 1));
  if (query.has("units")) return to;
  query.set("units", "metric");
  return `${path}?${query}${hash === undefined ? "" : `#${hash}`}`;
}

/** The app's internal link: a reader who chose metric stays metric on every in-page link (no JS needed). */
export function Link(props: LinkProps) {
  const metric = new URLSearchParams(useLocation().search).get("units") === "metric";
  return <RouterLink {...props} to={metric && typeof props.to === "string" ? withMetric(props.to) : props.to} />;
}
