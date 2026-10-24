// SPDX-License-Identifier: MIT
import type { Metric, Units } from "./contracts";

export const CAMERA_LABELS = { skycam_east: "East sky", skycam_west: "West sky", frontyard_1: "Front yard" } as const;
export function localTime(instant: string): string {
  return new Intl.DateTimeFormat("en-US", { timeZone: "America/Denver", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23", timeZoneName: "longOffset" }).format(new Date(instant));
}
export function localDate(instant: string): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "America/Denver", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(instant));
}
export function metricValue(value: number | null, unit: string, units: Units): string {
  if (value === null) return "—";
  let converted = value;
  let label = unit;
  if (unit === "degC") { converted = units === "us" ? value * 9 / 5 + 32 : value; label = units === "us" ? "°F" : "°C"; }
  if (unit === "m/s") { converted = units === "us" ? value * 2.2369362921 : value; label = units === "us" ? "mph" : "m/s"; }
  if (unit === "km") { converted = units === "us" ? value / 1.609344 : value; label = units === "us" ? "mi" : "km"; }
  if (unit === "mm/h") { converted = units === "us" ? value / 25.4 : value; label = units === "us" ? "in/h" : "mm/h"; }
  if (unit === "mm") { converted = units === "us" ? value / 25.4 : value; label = units === "us" ? "in" : "mm"; }
  if (unit === "mb" || unit === "hPa") { converted = units === "us" ? value * 0.0295299831 : value; label = units === "us" ? "inHg" : "hPa"; }
  return `${new Intl.NumberFormat("en-US", { maximumFractionDigits: unit === "mm" || unit === "mb" || unit === "hPa" ? 2 : 1 }).format(converted)}${unit === "%" ? "" : " "}${label}`;
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
export function metricLabel(metric: string): string {
  return ({ temperature: "Temperature", air_temperature: "Temperature", wind_gust: "Wind gust", wind_speed: "Wind speed", humidity: "Humidity", relative_humidity: "Humidity", precipitation: "Rain", rain_accumulation: "Rain", pressure: "Pressure", station_pressure: "Pressure" } as Record<string, string>)[metric] || metric.replaceAll("_", " ");
}
export function dateShift(value: string, offset: number): string {
  const day = new Date(`${value}T12:00:00Z`);
  day.setUTCDate(day.getUTCDate() + offset);
  return day.toISOString().slice(0, 10);
}
