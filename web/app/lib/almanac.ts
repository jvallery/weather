// SPDX-License-Identifier: MIT
import { calendarDate } from "./route-params";
import { dateShift, localDate } from "./format";

export const PRODUCTS = [
  { id: "record", label: "Records" },
  { id: "rain_day", label: "Rain calendar" },
  { id: "wind_rose_bin", label: "Wind rose" },
  { id: "astronomy", label: "Sun and moon" },
  { id: "comparison", label: "Compare periods" },
] as const;
export type Product = (typeof PRODUCTS)[number]["id"];
/** Record kinds the API ranks: observed extremes or totals, never normals. */
export const RECORDS = {
  warmest: { metric: "temperature", statistic: "max", label: "Warmest daily high" },
  coldest: { metric: "temperature", statistic: "min", label: "Coldest daily low" },
  gust: { metric: "wind_gust", statistic: "max", label: "Highest daily gust" },
  wettest: { metric: "precipitation", statistic: "sum", label: "Wettest day" },
} as const;
export const COMPARISONS = {
  rain: { metric: "precipitation", statistic: "sum", label: "Rain total" },
  mean: { metric: "temperature", statistic: "mean", label: "Mean temperature" },
  high: { metric: "temperature", statistic: "max", label: "Highest temperature" },
  low: { metric: "temperature", statistic: "min", label: "Lowest temperature" },
  gust: { metric: "wind_gust", statistic: "max", label: "Highest gust" },
} as const;
export const SECTORS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"] as const;
const MAX_DAYS = 366;
const clock = new Intl.DateTimeFormat("en-US", { timeZone: "America/Denver", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });

/** The UTC instant of 00:00 America/Denver on a local date. DST changes at 02:00, so midnight always exists once. */
export function localMidnight(date: string): string {
  for (const hour of ["06", "07"]) {
    const candidate = `${date}T${hour}:00:00Z`;
    const parts = Object.fromEntries(clock.formatToParts(new Date(candidate)).map((part) => [part.type, part.value]));
    if (`${parts.year}-${parts.month}-${parts.day}` === date && parts.hour === "00" && parts.minute === "00") return candidate;
  }
  throw new Response("Invalid calendar date", { status: 400 });
}
export function dayCount(first: string, last: string): number {
  return Math.round((Date.parse(`${last}T12:00:00Z`) - Date.parse(`${first}T12:00:00Z`)) / 86_400_000) + 1;
}
type Window = { from: string; to: string };
/** Default local-date windows; `to` is inclusive. Records default to about one #14 aggregate window (90 days) so the common view stays cheap; up to 366 days is a request away. */
export function defaultWindow(product: Product, today: string): Window {
  if (product === "record") return { from: dateShift(today, -89), to: today };
  if (product === "astronomy") return { from: today, to: dateShift(today, 6) };
  if (product === "comparison") return { from: dateShift(today, -6), to: today };
  return { from: dateShift(today, -29), to: today };
}
function window(url: URL, fromKey: string, toKey: string, fallback: Window): Window {
  const from = calendarDate(url.searchParams.get(fromKey) || fallback.from);
  const to = calendarDate(url.searchParams.get(toKey) || fallback.to);
  const days = dayCount(from, to);
  if (days < 1 || days > MAX_DAYS) throw new Response(`Select between 1 and ${MAX_DAYS} local days`, { status: 400 });
  return { from, to };
}
function choice<T extends Record<string, unknown>>(options: T, value: string | null, fallback: keyof T & string): keyof T & string {
  if (value === null || value === "") return fallback;
  if (!Object.hasOwn(options, value)) throw new Response("Invalid almanac selection", { status: 400 });
  return value;
}
export type AlmanacSelection = { product: Product; from: string; to: string; record: keyof typeof RECORDS; compare: keyof typeof COMPARISONS; compareFrom: string; compareTo: string };
/** Visitor parameters become one bounded, explicit almanac request (local days → UTC midnights). */
export function almanacSelection(url: URL, now: number): { selection: AlmanacSelection; params: URLSearchParams } {
  const requested = url.searchParams.get("product") || "record";
  const product = PRODUCTS.find((item) => item.id === requested)?.id;
  if (!product) throw new Response("Unknown almanac section", { status: 400 });
  const today = localDate(new Date(now).toISOString());
  const { from, to } = window(url, "from", "to", defaultWindow(product, today));
  const record = choice(RECORDS, url.searchParams.get("record"), "warmest");
  const compare = choice(COMPARISONS, url.searchParams.get("compare"), "rain");
  const previous = { from: dateShift(from, -dayCount(from, to)), to: dateShift(from, -1) };
  const other = window(url, "compare_from", "compare_to", previous);
  const params = new URLSearchParams({ product, start: localMidnight(from), end: localMidnight(dateShift(to, 1)) });
  if (product === "record") { params.set("metric", RECORDS[record].metric); params.set("statistic", RECORDS[record].statistic); }
  if (product === "comparison") {
    params.set("metric", COMPARISONS[compare].metric); params.set("statistic", COMPARISONS[compare].statistic);
    params.set("comparison_start", localMidnight(other.from)); params.set("comparison_end", localMidnight(dateShift(other.to, 1)));
  }
  return { selection: { product, from, to, record, compare, compareFrom: other.from, compareTo: other.to }, params };
}
export function percent(fraction: number): string {
  return `${(fraction * 100).toFixed(fraction > 0 && fraction < 0.001 ? 2 : 1)}%`;
}
export function hours(seconds: number): string {
  const whole = Math.floor(seconds / 3600);
  const minutes = Math.round((seconds % 3600) / 60);
  return minutes ? `${whole} h ${minutes} min` : `${whole} h`;
}
const REASON_TEXT: Record<string, string> = {
  no_observations: "No observations",
  partial_coverage: "Partial coverage",
  below_record_coverage: "Below 95% coverage, not ranked",
  before_history: "Before station history",
  inadequate_coverage: "Below 95% coverage",
  site_not_configured: "Site not configured",
  solar_events_unavailable: "Sun events unavailable",
  no_moonrise: "No moonrise this day",
  no_moonset: "No moonset this day",
};
export function reasonText(reasons: readonly string[]): string {
  return reasons.map((reason) => REASON_TEXT[reason] || reason.replaceAll("_", " ")).join(" · ");
}
