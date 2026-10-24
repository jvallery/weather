// SPDX-License-Identifier: MIT
import type { Metric, MetricDefinition, Units } from "./contracts";

export const CAMERA_LABELS = { skycam_east: "East sky", skycam_west: "West sky", frontyard_1: "Front yard" } as const;
export function localTime(instant: string): string {
  return new Intl.DateTimeFormat("en-US", { timeZone: "America/Denver", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23", timeZoneName: "longOffset" }).format(new Date(instant));
}
export function localDate(instant: string): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "America/Denver", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(instant));
}
const preferredUS: Record<string, string> = { degC: "degF", "m/s": "mph", mm: "in", hPa: "inHg", km: "mi" };
export function conversionAvailable(unit: string, units: Units, definition?: MetricDefinition): boolean {
  return units !== "us" || !preferredUS[unit] || (definition?.canonical_unit === unit && definition.conversions.some((recipe) => recipe.unit === preferredUS[unit]));
}
export function metricValue(value: number | null, unit: string, units: Units, definition?: MetricDefinition): string {
  if (value === null) return "—";
  const target = units === "us" ? preferredUS[unit] || unit : unit;
  const recipe = definition?.canonical_unit === unit ? definition.conversions.find((item) => item.unit === target) : undefined;
  const converted = recipe ? value * recipe.scale_numerator / recipe.scale_denominator + recipe.offset_numerator / recipe.offset_denominator : value;
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
