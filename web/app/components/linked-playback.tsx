// SPDX-License-Identifier: MIT
import { useQueries, useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent, type MouseEvent, type ReactNode } from "react";
import { Link } from "./unit-link";
import type { Units } from "../lib/contracts";
import { localTime } from "../lib/format";
import { binCenter, binOf, epochSeconds, frameAt, frameCenter, frameHalfWindowSeconds, frameOf, frameSeconds, gapSpans, instant, iso, isGap, KEOGRAM_BINS, parseTimeMap, resolveSeek, type Instant, type Span, type TimeMap } from "../lib/time-map";
import { parseAggregates, parseKeograms, type Aggregates, type KeogramItem, type MetricRegistry, type Timelapse, type WeatherEvent } from "../lib/weather-api";
import { EventStrip } from "./events";
import { binAt, binValue, LinkedPlots, PLOT_PANELS, plotBins, plotQueries, plotResolution, seriesLabel, seriesSummary } from "./linked-plots";
import { VideoPlayer, type SeekRequest } from "./video-player";

async function json(url: string, signal: AbortSignal): Promise<unknown> {
  const response = await fetch(url, { signal, headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error("unavailable");
  return response.json();
}
const duration = (seconds: number) => seconds >= 3600 ? `${(seconds / 3600).toFixed(1)} h` : seconds >= 60 ? `${Math.round(seconds / 60)} min` : `${seconds.toFixed(1)} s`;

/** The keogram strip. With the segment's /api/v1/keograms entry (#37) it uses that entry's state,
 * image, exact bin map and gap runs; until that arrives (or if it cannot be read) the catalog's. */
function Keogram({ video, keogram, span: segment, cursor, onSelect }: { video: Timelapse; keogram: KeogramItem | null; span: Span; cursor: Instant | null; onSelect: (at: Instant) => void }) {
  const marker = useRef<HTMLDivElement>(null);
  const state = keogram ? keogram.state : video.keogram_state;
  const image = keogram ? keogram.image : video.keogram;
  const visible = (state === "published" || state === "partial") && image !== null;
  const start = keogram ? instant(keogram.bins.start) : null, end = keogram ? instant(keogram.bins.end) : null;
  const span = start !== null && end !== null && start < end ? { start, end } : segment;
  const bin = cursor === null ? null : binOf(span, KEOGRAM_BINS, cursor);
  const gap = bin !== null && !!keogram?.gaps.some(([first, last]) => first <= bin && bin < last);
  useEffect(() => {
    if (!marker.current) return;
    marker.current.hidden = bin === null;
    if (bin !== null) marker.current.style.left = `${((bin + 0.5) / KEOGRAM_BINS) * 100}%`;
  }, [bin]);
  if (!visible) {
    const label = state === "pending" || state === "withdrawn" || state === "failed" ? state : "unavailable";
    const reason = keogram && (state === "failed" || state === "unavailable") && keogram.reason ? ` Reason: ${keogram.reason}.` : "";
    return <p className="notice" id="keogram-state">Keogram {label} · the recording still plays and the plots stay linked.{reason}</p>;
  }
  const half = epochSeconds(span.end - span.start) / KEOGRAM_BINS / 2;
  const choose = (index: number) => onSelect(binCenter(span, KEOGRAM_BINS, Math.min(KEOGRAM_BINS - 1, Math.max(0, index))));
  const key = (event: KeyboardEvent<HTMLDivElement>) => {
    const from = bin ?? -1;
    const next = { ArrowRight: from + 1, ArrowUp: from + 1, ArrowLeft: from - 1, ArrowDown: from - 1, PageUp: from + 60, PageDown: from - 60, Home: 0, End: KEOGRAM_BINS - 1 }[event.key];
    if (next === undefined) return;
    event.preventDefault(); choose(next);
  };
  // The image's x axis is the bin axis: 1,440 equal elapsed-UTC bins (D10).
  const click = (event: MouseEvent<HTMLDivElement>) => { const box = event.currentTarget.getBoundingClientRect(); choose(Math.floor(((event.clientX - box.left) / box.width) * KEOGRAM_BINS)); };
  const text = bin === null ? "No bin selected" : `Bin ${bin + 1} of ${KEOGRAM_BINS}, centred ${localTime(iso(binCenter(span, KEOGRAM_BINS, bin)))}${gap ? ", no capture" : ""}, ±${half.toFixed(1)} s`;
  return <figure className="keogram"><figcaption id="keogram-caption">Keogram · the whole recording as one strip; select a point to jump there ({KEOGRAM_BINS.toLocaleString("en-US")} bins of ±{half.toFixed(1)} s){state === "partial" ? " · partial: missing bins stay visible" : ""}</figcaption>
    <div className="keogram-strip" role="slider" tabIndex={0} aria-label="Keogram bin · arrow keys step one bin and seek the video" aria-describedby="keogram-caption" aria-valuemin={1} aria-valuemax={KEOGRAM_BINS} aria-valuenow={(bin ?? 0) + 1} aria-valuetext={text} onKeyDown={key} onClick={click}>
      <img src={image!.url} alt="" width={image!.width ?? undefined} height={image!.height ?? undefined} />
      <div ref={marker} className="keogram-cursor" hidden />
    </div></figure>;
}

/**
 * The route-local controller (D13): it owns the media revision's sidecar time map and the source
 * UTC cursor. Playback moves the cursor; plot, keogram, event and time-input selections seek
 * through the same map. A playback report only moves the cursor when the frame changes, so a
 * seek never echoes back as a second seek.
 */
export function LinkedPlayback({ video, requested, events, units, registry, side = null }: { video: Timelapse; requested: string | null; events: readonly WeatherEvent[] | null; units: Units; registry: MetricRegistry | null; side?: ReactNode }) {
  const span = useMemo<Span>(() => ({ start: instant(video.source_interval.start)!, end: instant(video.source_interval.end)! }), [video]);
  const sidecar = useQuery({
    queryKey: ["sidecar", video.video_id, video.revision, video.sidecar?.url],
    enabled: video.sidecar !== null, staleTime: Infinity,
    queryFn: async ({ signal }) => parseTimeMap(await json(video.sidecar!.url, signal), video),
  });
  const map: TimeMap | null = sidecar.data ?? null;
  const keogram = useQuery({
    queryKey: ["keogram", video.camera, video.cycle_date, video.segment], staleTime: 60_000,
    queryFn: async ({ signal }) => {
      const page = parseKeograms(await json(`/api/v1/keograms?${new URLSearchParams({ cycle_date: video.cycle_date, camera: video.camera, segment: video.segment })}`, signal));
      return page?.items.find((item) => item.camera === video.camera && item.segment === video.segment) ?? null;
    },
  });
  const queries = useMemo(() => plotQueries(span), [span]);
  const plotted = useQueries({
    queries: queries.map((query) => ({
      queryKey: ["aggregates", query.toString()], staleTime: 60_000,
      queryFn: async ({ signal }: { signal: AbortSignal }) => { const parsed = parseAggregates(await json(`/api/v1/weather/aggregates?${query}`, signal)); if (!parsed) throw new Error("invalid"); return parsed; },
    })),
  });
  const [temperature, gust, rain] = plotted.map((result) => result.data);
  const responses = useMemo<Aggregates[] | null>(() => (temperature && gust && rain ? [temperature, gust, rain] : null), [temperature, gust, rain]);
  const bins = useMemo(() => (responses ? plotBins(span, responses) : []), [span, responses]);
  const gaps = useMemo(() => (map ? gapSpans(map) : []), [map]);

  const [cursor, setCursor] = useState<Instant | null>(null);
  const [seek, setSeek] = useState<SeekRequest | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const serial = useRef(0);
  const seekTo = useCallback((target: Instant | Span, bookmark = true): boolean => {
    if (!map) { setNotice("The source-time map is unavailable, so seeking is disabled; playback still works."); return false; }
    const resolved = resolveSeek(map, target);
    if (!resolved) { setNotice("That time is outside this recording's source interval; the seek was rejected."); return false; }
    setNotice(null); setCursor(resolved.at);
    serial.current += 1; setSeek({ seconds: frameSeconds(map, resolved.frame), serial: serial.current });
    if (bookmark) {
      const url = new URL(window.location.href); url.searchParams.set("t", iso(resolved.at));
      window.history.replaceState(window.history.state, "", url);
    }
    return true;
  }, [map]);
  const onTime = useCallback((seconds: number) => {
    if (!map) return;
    const frame = frameAt(map, seconds);
    if (frame !== null) setCursor((current) => (current !== null && frameOf(map, current) === frame ? current : frameCenter(map, frame)));
  }, [map]);
  // A bookmark seeks once the map is known; without one the cursor rests on the first frame.
  const started = useRef(false);
  useEffect(() => {
    if (!map || started.current) return;
    started.current = true;
    const at = requested ? instant(requested) : null;
    if (at === null || !seekTo(at, false)) {
      setCursor(frameCenter(map, 0));
      if (requested) setNotice("The requested time is outside this recording or has no mapped frame; playback starts at the beginning.");
    }
  }, [map, requested, seekTo]);

  const frame = map && cursor !== null ? frameOf(map, cursor) : null;
  const bin = binAt(bins, cursor);
  const resolution = plotResolution(span);
  const partialLabel = map?.slotFraction !== null && map?.slotFraction !== undefined ? `Partial coverage · ${(map.slotFraction * 100).toFixed(1)}% of the source interval was captured; this recording has gaps.` : null;
  const station = useMemo(() => !responses ? null : <>
    <ul className="plot-summary" id="plot-summary">{PLOT_PANELS.flatMap((panel) => panel.series.map(([metric, statistic]) => <li key={`${metric}-${statistic}`}>{seriesSummary(bins, metric, statistic, units, registry)}</li>))}</ul>
    <details><summary>Plotted values table</summary><div className="table-wrap" tabIndex={0} role="region" aria-label="Plotted values table"><table><caption>One row per {resolution / 60}-minute interval</caption><thead><tr><th scope="col">Interval start · America/Denver</th>{PLOT_PANELS.flatMap((panel) => panel.series.map(([metric, statistic]) => <th scope="col" key={`${metric}-${statistic}`}>{seriesLabel(metric, statistic, registry)}</th>))}</tr></thead>
      <tbody>{bins.map((row) => <tr key={String(row.start)}><th scope="row"><time dateTime={iso(row.start)}>{localTime(iso(row.start))}</time></th>{PLOT_PANELS.flatMap((panel) => panel.series.map(([metric, statistic]) => <td key={`${metric}-${statistic}`}>{binValue(row, metric, statistic, units, registry)}</td>))}</tr>)}</tbody></table></div></details>
  </>, [bins, registry, resolution, responses, units]);

  const readout = <p className="cursor-readout" id="cursor-readout" data-utc={cursor === null ? "" : iso(cursor)} data-frame={frame ?? ""}>{cursor === null || frame === null || !map ? "No source time selected." : <>
    <time dateTime={iso(cursor)}>{localTime(iso(cursor))}</time> · frame {(frame + 1).toLocaleString("en-US")} of {map.frames.toLocaleString("en-US")} · ±{frameHalfWindowSeconds(map).toFixed(2)} s
    {isGap(map, frame) && <strong className="fault"> · Capture gap: this frame is a slate with no observed exposure.</strong>}
    {bin !== null && <span> · {PLOT_PANELS.flatMap((panel) => panel.series.map(([metric, statistic]) => `${seriesLabel(metric, statistic, registry)} ${binValue(bins[bin], metric, statistic, units, registry)}`)).join(" · ")}</span>}
  </>}</p>;
  return <>
    <div className="theater">
      <div className="theater-main">
        <VideoPlayer key={`${video.video_id}-${video.revision}`} video={video} seek={seek} onTime={onTime} coverageLabel={partialLabel} />
        <section aria-labelledby="cursor-title"><h2 id="cursor-title" className="screen-reader">Source-time cursor</h2>
          <Keogram video={video} keogram={keogram.data ?? null} span={span} cursor={cursor} onSelect={seekTo} />
          {!video.sidecar ? <p className="fault">This recording has no published sidecar, so its frames cannot be linked to source time.</p>
            : sidecar.isPending ? <p className="small">Loading the source-time map…<noscript> Linked plots and seeking need JavaScript; the sidecar JSON carries the same time map.</noscript></p>
              : !map ? <p className="fault">The source-time map is unavailable or does not match this recording, so seeking and linked plots are disabled. Playback still works.</p>
                : <>{readout}
                  {gaps.length > 0 && <p className="small">Capture gaps: {gaps.length} {gaps.length === 1 ? "range" : "ranges"} of slate frames totalling {duration(gaps.reduce((sum, gap) => sum + epochSeconds(gap.end - gap.start), 0))}, shown at their true length: {gaps.slice(0, 8).map((gap) => `${localTime(iso(gap.start))} for ${duration(epochSeconds(gap.end - gap.start))}`).join("; ")}{gaps.length > 8 ? "; …" : ""}.</p>}
                </>}
          {notice && <p className="notice" role="status">{notice}</p>}
          <form method="get" className="seek-form" onSubmit={(event) => {
            event.preventDefault();
            const value = new FormData(event.currentTarget).get("t");
            const at = typeof value === "string" ? instant(value.trim()) : null;
            if (at === null) setNotice("Enter a UTC time such as 2026-10-31T15:20:00Z."); else seekTo(at);
          }}><label>Seek to source time (UTC)<input name="t" defaultValue={requested ?? ""} placeholder={video.source_interval.start} autoComplete="off" spellCheck={false} /></label>{units === "metric" && <input type="hidden" name="units" value="metric" />}<button type="submit">Seek</button>
            {cursor !== null && <a href={`?t=${encodeURIComponent(iso(cursor))}${units === "metric" ? "&units=metric" : ""}`}>Link to this moment</a>}</form>
        </section>
      </div>
      {side && <aside className="theater-side" aria-label="About this recording">{side}</aside>}
    </div>
    <section className="section" aria-labelledby="station-title"><div className="section-head"><div><span className="eyebrow">One station, the same values on every camera</span><h2 id="station-title">Station measurements · shared by all cameras</h2></div><Link to={`/weather/history/?${new URLSearchParams({ start: iso(span.start), end: iso(span.end) })}`}>Open this interval in weather history →</Link></div>
      <p className="small">Each point is one {resolution / 60}-minute interval of this recording&apos;s source time. Intervals without observations are gaps, never zero; capture gaps are shaded at their true length. Click a plot, or focus it and use the arrow keys, to seek.</p>
      {plotted.some((result) => result.isError) ? <p className="fault">Weather history API unavailable. No substitute values are plotted.</p>
        : !responses ? <p className="small">Loading measured weather…</p>
          : bins.length === 0 ? <p>No measured intervals were returned for this recording.</p>
            : <><LinkedPlots span={span} bins={bins} gaps={gaps} cursor={cursor} onSelect={seekTo} units={units} registry={registry} describedBy="plot-summary" />{station}</>}
    </section>
    <EventStrip title="Weather events in this recording" events={events} video={video} onSeek={map ? (event) => { seekTo(eventSpan(event)); } : undefined} />
  </>;
}
function eventSpan(event: WeatherEvent): Span {
  return { start: instant(event.source_interval.start) ?? 0n, end: instant(event.source_interval.end) ?? 0n };
}
