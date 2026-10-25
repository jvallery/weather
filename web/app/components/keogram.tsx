// SPDX-License-Identifier: MIT
import { useState, type MouseEvent } from "react";
import { Link, useNavigate } from "react-router";
import { CAMERA_LABELS, localTime } from "../lib/format";
import type { KeogramItem } from "../lib/weather-api";
import { Status } from "./common";

const BINS = 1440;

/** Bin i's UTC interval in whole milliseconds (the API's map is exact; display needs no more). */
export function binInterval(keogram: KeogramItem, index: number): { start: string; middle: string; end: string } {
  const origin = Date.parse(keogram.bins.start);
  const step = keogram.bins.step_seconds.numerator * 1000 / keogram.bins.step_seconds.denominator;
  const at = (offset: number) => new Date(Math.floor(origin + offset * step)).toISOString();
  return { start: at(index), middle: at(index + 0.5), end: at(index + 1) };
}
export function binObserved(keogram: KeogramItem, index: number): boolean {
  return !keogram.gaps.some(([first, last]) => first <= index && index < last);
}
export function seekHref(keogram: KeogramItem, index: number): string {
  return `/timelapses/${keogram.camera}/${keogram.cycle_date}/${keogram.segment}/?t=${encodeURIComponent(binInterval(keogram, index).middle)}`;
}

/** A segment keogram: time runs left to right, one column per bin. Selecting a bin (pointer, or the
 * slider's arrow keys) opens the matching recording at that bin's source time. */
export function KeogramPanel({ keogram, full = false }: { keogram: KeogramItem; full?: boolean }) {
  const [selected, setSelected] = useState(BINS / 2);
  const navigate = useNavigate();
  const segment = keogram.segment === "daytime" ? "Daytime" : "Overnight";
  const label = `${CAMERA_LABELS[keogram.camera]} · ${segment} keogram`;
  const picture = full ? keogram.image : keogram.thumbnail;
  if (!["published", "partial"].includes(keogram.state) || !picture) {
    return <p className="keogram-state">Keogram <Status state={keogram.state} reason={keogram.reason} /></p>;
  }
  const bin = binInterval(keogram, selected);
  const observed = binObserved(keogram, selected);
  const precision = keogram.bins.step_seconds.numerator / keogram.bins.step_seconds.denominator / 2;
  const id = `keogram-${keogram.keogram_id}`;
  const pick = (event: MouseEvent<HTMLImageElement>) => {
    const box = event.currentTarget.getBoundingClientRect();
    if (box.width <= 0) return;
    const index = Math.min(BINS - 1, Math.max(0, Math.floor((event.clientX - box.left) / box.width * BINS)));
    setSelected(index);
    void navigate(seekHref(keogram, index));
  };
  return <figure className="keogram-panel" aria-labelledby={`${id}-caption`}>
    <img src={picture.url} width={picture.width ?? undefined} height={picture.height ?? undefined} onClick={pick}
      alt={`${label}: the camera's region averaged per ${Math.round(precision * 2)} s bin from ${localTime(keogram.bins.start)} to ${localTime(keogram.bins.end)}; ${keogram.gaps.length ? `${BINS - (keogram.observed_bins ?? BINS)} of ${BINS} bins have no capture and show a checkered gap` : "every bin observed"}.`} />
    <figcaption id={`${id}-caption`}>{label} · <Status state={keogram.state} reason={keogram.reason} /> · {keogram.observed_bins ?? 0} of {BINS} bins observed</figcaption>
    <label className="keogram-bin" htmlFor={`${id}-bin`}>Keogram time
      <input id={`${id}-bin`} type="range" min={0} max={BINS - 1} step={1} value={selected}
        aria-valuetext={`${localTime(bin.middle)} · ${observed ? "observed" : "no capture"}`}
        onChange={(event) => setSelected(Number(event.target.value))} />
    </label>
    <p className="source-time" aria-live="polite">Bin {selected + 1} of {BINS}: <time dateTime={bin.start}>{localTime(bin.start)}</time> → <time dateTime={bin.end}>{localTime(bin.end)}</time> · ±{precision.toFixed(1)} s · {observed ? "observed" : "no capture (gap)"}</p>
    <p><Link className="button-link" to={seekHref(keogram, selected)}>Open the recording at {localTime(bin.middle)}</Link></p>
  </figure>;
}
