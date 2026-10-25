// SPDX-License-Identifier: MIT
import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate, useNavigationType } from "react-router";
import { hours, percent } from "../lib/almanac";
import type { Units } from "../lib/contracts";
import { CAMERA_LABELS, localDate, localTime } from "../lib/format";
import {
  cameraLabel, cycleDate, defaultUnit, draftFromParams, emptyFilter, groupHits, MAX_PREDICATES, MAX_QUERY, MAX_TOP_K, measureLabel, MEASURES,
  measurementValue, OPERATOR_LABELS, OPERATORS, paramsFromDraft, paramsFromQuery, readOutcome, searchRequest, seekLink, UNIT_LABELS, validate,
  type Draft, type DraftFilter, type Measure, type Outcome, type SearchHit, type SearchQuery, type SearchResults, type VideoResult,
} from "../lib/search";
import { parseRegistry, type MetricRegistry } from "../lib/weather-api";
export const meta = () => [{ title: "Search recorded weather · Weather" }];

type View = { kind: "idle" } | { kind: "loading" } | (Outcome & { text: string });
const SEARCH_DEADLINE_MS = 10_000;
/**
 * One POST per submitted URL (or explicit "Search again"); none while typing, no automatic
 * retry, no cache. A newer search aborts the older request, and a late older response is ignored.
 */
function useSearch(query: SearchQuery | null, navigation: string): [View, () => void] {
  const [attempt, setAttempt] = useState(0);
  const body = query ? JSON.stringify(searchRequest(query)) : null;
  const [view, setView] = useState<View>(body ? { kind: "loading" } : { kind: "idle" });
  useEffect(() => {
    if (body === null) { setView({ kind: "idle" }); return; }
    const controller = new AbortController();
    let current = true;
    const timer = setTimeout(() => controller.abort(), SEARCH_DEADLINE_MS);
    const text = (JSON.parse(body) as { query: string }).query;
    setView({ kind: "loading" });
    fetch("/api/v1/search", { method: "POST", body, signal: controller.signal, cache: "no-store", credentials: "omit", redirect: "error", headers: { "Content-Type": "application/json", Accept: "application/json" } })
      .then(readOutcome)
      .catch((): Outcome => ({ kind: "unavailable" }))
      .then((outcome) => { if (current) setView({ ...outcome, text }); })
      .finally(() => clearTimeout(timer));
    return () => { current = false; controller.abort(); clearTimeout(timer); };
  }, [body, navigation, attempt]);
  return [view, () => setAttempt((value) => value + 1)];
}

const PRECISION = { frame: "Single frame", interval: "Time window", clip: "Clip", segment: "Whole recording" } as const;
const SEGMENTS = { daytime: "Daytime", overnight: "Overnight" } as const;
function Interval({ value }: { value: { start: string; end: string } }) {
  return <><time dateTime={value.start}>{localTime(value.start)}</time> → <time dateTime={value.end}>{localTime(value.end)}</time> (exclusive)</>;
}
function ModeNotice({ data }: { data: SearchResults }) {
  if (data.mode === "hybrid_exact") return <p className="notice" data-mode="hybrid_exact">Ranked by meaning and words: hybrid exact search{data.profile_id ? ` · profile ${data.profile_id}` : ""}.</p>;
  if (data.mode === "numeric") return <p className="notice" data-mode="numeric">No search text: recordings that match the filters, newest first. Nothing is ranked by meaning or words.</p>;
  const reason = data.degraded_reason;
  if (reason === "embedding_unavailable") return <p className="notice notice-degraded" data-mode="lexical" role="alert">AI search is offline. These are word matches only (lexical fallback: embedding unavailable). Date, camera, segment and measured filters still apply in full.</p>;
  if (reason === "embedding_busy") return <p className="notice notice-degraded" data-mode="lexical" role="alert">AI search is busy. These are word matches only (lexical fallback: embedding busy). Date, camera, segment and measured filters still apply in full.</p>;
  if (reason === "lexical_no_terms") return <p className="notice notice-degraded" data-mode="lexical" role="alert">The search text has no searchable words and AI search is unavailable, so the text cannot match anything (lexical: no terms). Clear the text to browse by the filters alone.</p>;
  return <p className="notice" data-mode="lexical">Ranked by words (lexical search).</p>;
}
const modeLabel = (data: SearchResults) => data.mode === "hybrid_exact" ? "hybrid exact" : data.mode === "numeric" ? "filters only, newest first" : "lexical (words)";

function Window({ item }: { item: SearchHit }) {
  const link = seekLink(item);
  return <li>{PRECISION[item.evidence_precision]} · <Interval value={item.source_interval} /> · rank {item.rank} · {link && item.seek
    ? <Link to={link}>Open video at {localTime(item.seek.source_at)}</Link>
    : <span className="fault">Location unavailable{item.seek ? ": the seek falls outside this window or recording" : ": no playable position"}</span>}</li>;
}
function Card({ result, data, units, registry }: { result: VideoResult; data: SearchResults; units: Units; registry: MetricRegistry | null }) {
  const { video } = result;
  const poster = video.poster_asset_id && /^[0-9a-f-]{36}$/.test(video.poster_asset_id) ? `/media/${video.poster_asset_id}` : null;
  const camera = cameraLabel(video.camera_id), cycle = cycleDate(video);
  return <li className="card search-result" data-video={video.video_id}>
    <h3>{camera} · {SEGMENTS[video.segment]} · solar cycle {cycle}</h3>
    {poster && <img src={poster} alt={`Poster frame: ${camera}, ${SEGMENTS[video.segment].toLowerCase()} recording of solar cycle ${cycle}`} loading="lazy" />}
    <p className="source-time">Recording: <Interval value={video.video_interval} /></p>
    <p><span className="badge">Effective public description (AI-generated or human-edited) · analysis mode {video.analysis_mode}</span></p>
    <p>{video.snippet}</p>
    <p className="small">Ranking: {modeLabel(data)} · media revision {video.media_revision}</p>
    <h4>Matched {result.hits.length === 1 ? "window" : `windows (${result.hits.length})`}</h4>
    <ul className="segment-list">{result.hits.map((item) => <Window key={item.document_id} item={item} />)}</ul>
    {result.measurements.length > 0 && <><h4>Measured · applies to the whole video</h4>
      <p className="small">Station measurements over the entire recording, which may be broader than a matched window; not a time within it.</p>
      <ul className="segment-list">{result.measurements.map((item) => <li key={`${item.metric}-${item.statistic}-${item.interval.start}`}>
        {measureLabel(item)}: <strong>{measurementValue(item, units, registry?.metrics.find((entry) => entry.metric === item.metric))}</strong> · over <Interval value={item.interval} /> · coverage {percent(item.coverage.fraction)} of {hours(item.coverage.expected_seconds)} · {item.evidence_count} observations · evidence hash <span className="monospace">{item.evidence_hash ?? "none"}</span>
      </li>)}</ul></>}
  </li>;
}
function Results({ view, units, retry }: { view: View; units: Units; retry: () => void }) {
  const measured = view.kind === "results" && view.data.hits.some((item) => item.matched_measurements.length > 0);
  const registry = useQuery({
    queryKey: ["metric-registry"], enabled: measured, staleTime: Infinity,
    queryFn: async ({ signal }) => { const response = await fetch("/api/v1/weather/metrics", { signal, headers: { Accept: "application/json" } }); return response.ok ? parseRegistry(await response.json()) : null; },
  }).data ?? null;
  if (view.kind === "idle") return null;
  if (view.kind === "loading") return <p className="notice" role="status" aria-busy="true">Searching recordings…</p>;
  if (view.kind === "invalid") return <div className="search-state" role="alert"><h3>Search rejected</h3><p className="fault">{view.code === "range_too_large" ? "Measured filters cover at most 31 days per search. Narrow the dates or split the search." : "The search service rejected a filter in this search."} Nothing was searched.</p></div>;
  if (view.kind === "busy") return <div className="search-state" role="alert"><h3>Search is busy</h3><p>Too many searches are running. Try again {view.retryAfter ? `in ${view.retryAfter} ${view.retryAfter === 1 ? "second" : "seconds"}` : "shortly"}; nothing is retried automatically.</p><button type="button" onClick={retry}>Search again</button></div>;
  if (view.kind === "unavailable") return <div className="search-state" role="alert"><h3>Search unavailable</h3><p className="fault">The search service did not answer. This is not an empty result: no recordings are shown in its place.</p><button type="button" onClick={retry}>Search again</button></div>;
  const groups = groupHits(view.data.hits);
  return <>
    <p role="status">{view.text.trim() ? `Results for “${view.text}”` : "Results for the filters alone"} · {groups.length} {groups.length === 1 ? "recording" : "recordings"}</p>
    <ModeNotice data={view.data} />
    {measured && units === "us" && !registry && <p className="small">Unit conversion unavailable: measured values are shown in source units.</p>}
    {groups.length === 0 ? view.data.degraded_reason !== "lexical_no_terms" && <p className="search-empty">No recordings match this search. Try other words, wider dates or fewer filters.</p>
      : <ol className="search-results">{groups.map((result) => <Card key={result.video.video_id} result={result} data={view.data} units={units} registry={registry} />)}</ol>}
  </>;
}

function choices(known: readonly string[], selected: readonly string[]) { return [...known, ...selected.filter((value) => !known.includes(value))]; }
function FilterRow({ filter, index, units, onChange, onRemove }: { filter: DraftFilter; index: number; units: Units; onChange: (filter: DraftFilter) => void; onRemove: () => void }) {
  const measure = Object.hasOwn(MEASURES, filter.measure) ? MEASURES[filter.measure as Measure] : null;
  return <fieldset className="filters search-filter"><legend>Measured filter {index + 1}</legend>
    <label>Measurement<select value={filter.measure} onChange={(event) => { const next = event.target.value as Measure; onChange({ ...filter, measure: next, unit: (MEASURES[next].units as readonly string[]).includes(filter.unit) ? filter.unit : defaultUnit(next, units) }); }}>
      {choices(Object.keys(MEASURES), [filter.measure]).map((key) => <option key={key} value={key}>{Object.hasOwn(MEASURES, key) ? MEASURES[key as Measure].label : `Unknown: ${key}`}</option>)}</select></label>
    <label>Comparison<select value={filter.op} onChange={(event) => onChange({ ...filter, op: event.target.value })}>
      {choices(Object.keys(OPERATORS), [filter.op]).map((key) => <option key={key} value={key}>{Object.hasOwn(OPERATORS, key) ? `${OPERATORS[key as keyof typeof OPERATORS]} ${OPERATOR_LABELS[key as keyof typeof OPERATORS]}` : `Unknown: ${key}`}</option>)}</select></label>
    <label>Value<input inputMode="decimal" autoComplete="off" value={filter.value} onChange={(event) => onChange({ ...filter, value: event.target.value })} /></label>
    <label>Unit<select value={filter.unit} onChange={(event) => onChange({ ...filter, unit: event.target.value })}>
      {choices(measure?.units ?? [], [filter.unit]).map((unit) => <option key={unit} value={unit}>{UNIT_LABELS[unit] ?? `Unknown: ${unit}`}</option>)}</select></label>
    <label>Minimum coverage (%)<input inputMode="decimal" autoComplete="off" value={filter.coverage} onChange={(event) => onChange({ ...filter, coverage: event.target.value })} /></label>
    <button type="button" onClick={onRemove}>Remove filter {index + 1}</button>
  </fieldset>;
}
function SearchForm({ initial, onSubmit }: { initial: Draft; onSubmit: (draft: Draft) => void }) {
  const [draft, setDraft] = useState(initial);
  const units: Units = draft.units === "metric" ? "metric" : "us";
  const toggle = (key: "cameras" | "segments", value: string, checked: boolean) => setDraft({ ...draft, [key]: checked ? [...draft[key], value] : draft[key].filter((item) => item !== value) });
  const submit = (event: FormEvent) => { event.preventDefault(); onSubmit(draft); };
  return <form method="get" action="/search/" noValidate onSubmit={submit} className="search-form" aria-label="Search recordings">
    <label htmlFor="search-text">Words to find in recording descriptions (optional, at most {MAX_QUERY} characters)</label>
    <input id="search-text" name="q" type="search" autoComplete="off" value={draft.q} onChange={(event) => setDraft({ ...draft, q: event.target.value })} />
    <div className="filters">
      <label>First calendar day (America/Denver)<input name="from" type="date" value={draft.from} onChange={(event) => setDraft({ ...draft, from: event.target.value })} /></label>
      <label>Last calendar day (inclusive)<input name="to" type="date" value={draft.to} onChange={(event) => setDraft({ ...draft, to: event.target.value })} /></label>
      <label>Results<input name="top_k" type="number" min={1} max={MAX_TOP_K} step={1} value={draft.topK} onChange={(event) => setDraft({ ...draft, topK: event.target.value })} /></label>
      <label>Display units<select name="units" value={draft.units} onChange={(event) => setDraft({ ...draft, units: event.target.value })}>{choices(["us", "metric"], [draft.units]).map((value) => <option key={value} value={value}>{value === "us" ? "US customary" : value === "metric" ? "Metric" : `Unknown: ${value}`}</option>)}</select></label>
    </div>
    <fieldset className="filters"><legend>Cameras (none checked: all)</legend>{choices(Object.keys(CAMERA_LABELS), draft.cameras).map((value) => <label key={value} className="check"><input type="checkbox" name="camera" value={value} checked={draft.cameras.includes(value)} onChange={(event) => toggle("cameras", value, event.target.checked)} />{CAMERA_LABELS[value as keyof typeof CAMERA_LABELS] ?? `Unknown: ${value}`}</label>)}</fieldset>
    <fieldset className="filters"><legend>Segments (none checked: both)</legend>{choices(Object.keys(SEGMENTS), draft.segments).map((value) => <label key={value} className="check"><input type="checkbox" name="segment" value={value} checked={draft.segments.includes(value)} onChange={(event) => toggle("segments", value, event.target.checked)} />{SEGMENTS[value as keyof typeof SEGMENTS] ?? `Unknown: ${value}`}</label>)}</fieldset>
    {draft.filters.map((filter, index) => <FilterRow key={index} filter={filter} index={index} units={units} onChange={(next) => setDraft({ ...draft, filters: draft.filters.map((item, at) => at === index ? next : item) })} onRemove={() => setDraft({ ...draft, filters: draft.filters.filter((_, at) => at !== index) })} />)}
    <div className="actions">
      <button type="button" disabled={draft.filters.length >= MAX_PREDICATES} onClick={() => setDraft({ ...draft, filters: [...draft.filters, emptyFilter(units)] })}>Add measured filter</button>
      <button type="submit">Search</button>
    </div>
    <details><summary>How measured filters work</summary><ul className="segment-list">
      {Object.values(MEASURES).map((measure) => <li key={measure.label}><strong>{measure.label}</strong>: {measure.help}</li>)}
      <li>Each measured filter applies to the whole video segment, which may be broader than a matched window. Up to {MAX_PREDICATES} filters over at most 31 days.</li>
      <li>Minimum coverage is the share of the recording with valid station observations (default 95%). Unknown or under-covered values never satisfy a filter, even “= 0”: unknown rain is not “no rain”.</li>
    </ul></details>
  </form>;
}

export default function SearchPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const [today] = useState(() => localDate(new Date().toISOString()));
  const { draft, submitted } = useMemo(() => draftFromParams(new URLSearchParams(location.search), today), [location.search, today]);
  const { query, errors } = useMemo(() => submitted ? validate(draft) : { query: null, errors: [] }, [draft, submitted]);
  const [view, retry] = useSearch(query, location.key);
  const units: Units = query?.units ?? (draft.units === "metric" ? "metric" : "us");
  // The form keeps its state (and keyboard focus) across its own submits; any other navigation
  // (back/forward, a link) remounts it from the URL.
  const own = useNavigationType() !== "POP" && (location.state as { ownSubmit?: boolean } | null)?.ownSubmit === true;
  const [seen, setSeen] = useState(location.key);
  const [formVersion, setFormVersion] = useState(0);
  if (seen !== location.key) { setSeen(location.key); if (!own) setFormVersion((value) => value + 1); }
  const submit = (next: Draft) => {
    const valid = validate(next).query;
    navigate({ search: `?${valid ? paramsFromQuery(valid) : paramsFromDraft(next)}` }, { state: { ownSubmit: true } });
  };
  const request = query ? searchRequest(query) : null;
  return <><h1>Search recorded weather</h1>
    <p>Find timelapse recordings by words in their descriptions, by camera, segment and date, and by station measurements over each recording. Results cite their evidence; measured values apply to the whole video.</p>
    <noscript><p className="notice">Search runs in your browser and needs JavaScript. The archive, day pages and events work without it.</p></noscript>
    <SearchForm key={formVersion} initial={draft} onSubmit={submit} />
    <section aria-labelledby="results-heading"><h2 id="results-heading">Results</h2>
      {errors.length > 0 && <div className="fault" role="alert"><p>Check the search; nothing was sent:</p><ul>{errors.map((error) => <li key={error}>{error}</li>)}</ul></div>}
      {request && <p className="source-time">Recordings overlapping <Interval value={request} /> · calendar days in America/Denver · UTC {request.start} → {request.end}</p>}
      {!submitted && <p>Choose words or filters and select Search.</p>}
      <Results view={view} units={units} retry={retry} />
    </section>
  </>;
}
