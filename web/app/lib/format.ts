// SPDX-License-Identifier: MIT
import type { Units } from "./contracts";
import type { Metric, MetricDefinition } from "./weather-api";

export const CAMERA_LABELS = { skycam_east: "East sky", skycam_west: "West sky", frontyard_1: "Front yard" } as const;
// An unparseable instant from the API is shown as unavailable for that value only, never a render error.
export const TIME_UNAVAILABLE = "time unavailable";
function instantDate(instant: string): Date | null {
  const date = new Date(instant);
  return Number.isNaN(date.getTime()) ? null : date;
}
export function localTime(instant: string): string {
  const date = instantDate(instant);
  if (!date) return TIME_UNAVAILABLE;
  return new Intl.DateTimeFormat("en-US", { timeZone: "America/Denver", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23", timeZoneName: "longOffset" }).format(date);
}
export function plotTime(epochSeconds: number): string {
  const parts = new Intl.DateTimeFormat("en-US", { timeZone: "America/Denver", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZoneName: "longOffset" }).formatToParts(new Date(epochSeconds * 1000));
  const part = (name: Intl.DateTimeFormatPartTypes) => parts.find((item) => item.type === name)?.value || "";
  return `${part("month")}/${part("day")}/${part("year")}\n${part("hour")}:${part("minute")} ${part("timeZoneName")}`;
}
export function localDate(instant: string): string {
  const date = instantDate(instant);
  if (!date) return TIME_UNAVAILABLE;
  return new Intl.DateTimeFormat("en-CA", { timeZone: "America/Denver", year: "numeric", month: "2-digit", day: "2-digit" }).format(date);
}
const preferredUS: Record<string, string> = { degC: "degF", "m/s": "mph", mm: "in", hPa: "inHg", km: "mi" };
export function conversionAvailable(unit: string, units: Units, definition?: MetricDefinition): boolean {
  return units !== "us" || !preferredUS[unit] || (definition?.canonical_unit === unit && definition.conversions.some((recipe) => recipe.unit === preferredUS[unit]));
}
/** A difference between two values converts by scale only (a 1 °C change is 1.8 °F, not 33.8 °F). */
export function metricValue(value: number | null, unit: string, units: Units, definition?: MetricDefinition, difference = false): string {
  if (value === null) return "—";
  const target = units === "us" ? preferredUS[unit] || unit : unit;
  const recipe = definition?.canonical_unit === unit ? definition.conversions.find((item) => item.unit === target) : undefined;
  const offset = recipe && !difference ? recipe.offset_numerator / recipe.offset_denominator : 0;
  const converted = recipe ? value * recipe.scale_numerator / recipe.scale_denominator + offset : value;
  const selected = recipe?.unit || unit;
  const label = ({ degC: "°C", degF: "°F", deg: "°", "W/m2": "W/m²", "1": "", index: "" } as Record<string, string>)[selected] ?? selected;
  return `${new Intl.NumberFormat("en-US", { maximumFractionDigits: ["mm", "hPa"].includes(unit) ? 2 : 1 }).format(converted)}${label && selected !== "%" ? " " : ""}${label}`;
}
export function ageSeconds(observedAt: string | null, now: number): number | null {
  if (!observedAt) return null;
  const age = (now - Date.parse(observedAt)) / 1000;
  return Number.isFinite(age) && age >= 0 ? Math.floor(age) : null;
}
export function ageLabel(metric: Metric, now: number): string {
  const age = ageSeconds(metric.observed_at, now);
  if (age === null) return "Source time unavailable";
  const duration = age < 60 ? `${age} s` : age < 3600 ? `${Math.floor(age / 60)} min` : `${Math.floor(age / 3600)} h`;
  const stale = metric.freshness !== "fresh" || !metric.valid_until || Date.parse(metric.valid_until) <= now;
  return `${duration} old${stale ? " · stale" : ""}`;
}
export function metricLabel(metric: string, definition?: MetricDefinition): string {
  if (definition?.metric === metric) return definition.label;
  return ({ temperature: "Temperature", air_temperature: "Temperature", wind_gust: "Wind gust", wind_speed: "Wind speed", humidity: "Humidity", relative_humidity: "Humidity", precipitation: "Rain", rain_accumulation: "Rain", pressure: "Pressure", station_pressure: "Pressure" } as Record<string, string>)[metric] || metric.replaceAll("_", " ");
}
export function dateShift(value: string, offset: number): string {
  const day = new Date(`${value}T12:00:00Z`);
  day.setUTCDate(day.getUTCDate() + offset);
  return day.toISOString().slice(0, 10);
}

const SHORT_TIME = new Intl.DateTimeFormat("en-US", { timeZone: "America/Denver", hour: "numeric", minute: "2-digit" });
const SHORT_DATE_TIME = new Intl.DateTimeFormat("en-US", { timeZone: "America/Denver", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
/** "6:42 AM", America/Denver: for display where the page states the zone once. */
export function shortTime(instant: string): string {
  const date = instantDate(instant);
  return date ? SHORT_TIME.format(date) : TIME_UNAVAILABLE;
}
/** "Oct 27, 6:42 AM", America/Denver. */
export function shortDateTime(instant: string): string {
  const date = instantDate(instant);
  return date ? SHORT_DATE_TIME.format(date) : TIME_UNAVAILABLE;
}
/** A calendar date (YYYY-MM-DD) as words: "Tuesday, Oct 27, 2026"; `short` drops the weekday and year. */
export function dayTitle(value: string, style: "long" | "short" = "long"): string {
  const date = new Date(`${value}T12:00:00Z`);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("en-US", style === "long" ? { timeZone: "UTC", weekday: "long", month: "short", day: "numeric", year: "numeric" } : { timeZone: "UTC", month: "short", day: "numeric" }).format(date);
}
/** "October 2026" for YYYY-MM. */
export function monthTitle(month: string): string {
  const date = new Date(`${month}-15T12:00:00Z`);
  return Number.isNaN(date.getTime()) ? month : new Intl.DateTimeFormat("en-US", { timeZone: "UTC", month: "long", year: "numeric" }).format(date);
}
/** Elapsed seconds as "12 s", "4 min", "2 h", "3 d". */
export function duration(seconds: number): string {
  if (seconds < 60) return `${Math.max(0, Math.floor(seconds))} s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min`;
  if (seconds < 86_400) return `${Math.floor(seconds / 3600)} h`;
  return `${Math.floor(seconds / 86_400)} d`;
}
const POINTS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];
/** The 16-point compass name of a direction in degrees ("from"), or null. */
export function compass(degrees: number | null): string | null {
  if (degrees === null || !Number.isFinite(degrees)) return null;
  return POINTS[Math.round((((degrees % 360) + 360) % 360) / 22.5) % 16];
}
const REASON_WORDS: Record<string, string> = {
  low_coverage: "low coverage", no_observations: "no observations", no_capture: "no capture", no_eligible_capture: "no eligible capture",
  not_captured: "not captured", media_unavailable: "media unavailable", embargo: "inside the 10-minute delay", no_preview: "no preview yet",
  site_not_configured: "site not configured", stale: "stale", missing: "missing", invalid: "failed quality checks",
};
/** A reason code in words; an unknown code still shows, with its underscores as spaces. */
export function humanize(code: string | null | undefined): string {
  if (!code) return "";
  // Only a snake_case code is rewritten; any other text shows exactly as received (as escaped text).
  return REASON_WORDS[code] ?? (/^[a-z][a-z0-9_]*$/.test(code) ? code.replaceAll("_", " ") : code);
}
/** The UTC instant of local midnight (America/Denver) starting a calendar date. */
export function localDayStart(value: string): string {
  const midnight = Date.parse(`${value}T00:00:00Z`);
  // The offset in force at UTC midnight is the one at the following local midnight: DST changes at 02:00.
  const name = new Intl.DateTimeFormat("en-US", { timeZone: "America/Denver", timeZoneName: "shortOffset" }).formatToParts(new Date(midnight)).find((part) => part.type === "timeZoneName")?.value || "GMT-7";
  const hours = Number(/GMT([+-]\d+)/.exec(name)?.[1] ?? -7);
  return new Date(midnight - hours * 3_600_000).toISOString();
}
