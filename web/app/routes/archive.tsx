// SPDX-License-Identifier: MIT
import { useLoaderData, type LoaderFunctionArgs } from "react-router";
import { Link } from "../components/unit-link";
import { PageHead, Pager, SEGMENT_LABELS, Status, VideoLink } from "../components/common";
import { CAMERA_LABELS, localDate, monthTitle } from "../lib/format";
import { archiveRead } from "../lib/public-api.server";
import { catalogParams } from "../lib/route-params";
import type { ArchivePage } from "../lib/weather-api";
export const meta = () => [{ title: "Timelapses · Weather" }];
const CAMERAS = Object.keys(CAMERA_LABELS) as (keyof typeof CAMERA_LABELS)[];
const SEGMENTS = ["daytime", "overnight"] as const;
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
export async function loader({ request, params }: LoaderFunctionArgs) {
  const url = new URL(request.url);
  const selectedMonth = url.searchParams.get("month") || (params.year && params.month ? `${params.year}-${params.month}` : localDate(new Date().toISOString()).slice(0, 7));
  if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(selectedMonth)) throw new Response("Invalid archive month", { status: 400 });
  const filters = catalogParams(url);
  filters.delete("cursor");
  // A month is at most 31 × 6 recordings: two API pages cover it, read in order (the cursor is bound to the filters).
  const query = new URLSearchParams(filters);
  query.set("month", selectedMonth);
  const first = await archiveRead(request, query);
  let catalog: ArchivePage | null = first;
  if (first?.next_cursor) {
    const next = new URLSearchParams(query); next.set("cursor", first.next_cursor);
    const second = await archiveRead(request, next);
    catalog = second ? { ...second, items: [...first.items, ...second.items] } : first;
  }
  return { catalog, selectedMonth, today: localDate(new Date().toISOString()), filters: Object.fromEntries(filters) };
}
export default function Archive() {
  const { catalog, selectedMonth, today, filters } = useLoaderData<typeof loader>();
  const [year, month] = selectedMonth.split("-").map(Number);
  const days = new Date(Date.UTC(year, month, 0)).getUTCDate();
  const lead = new Date(Date.UTC(year, month - 1, 1)).getUTCDay();
  const previous = new Date(Date.UTC(year, month - 2, 1)).toISOString().slice(0, 7);
  const next = new Date(Date.UTC(year, month, 1)).toISOString().slice(0, 7);
  const link = (changes: Record<string, string | null>, target = selectedMonth) => {
    const query = new URLSearchParams(filters);
    for (const [key, value] of Object.entries(changes)) { if (value === null) query.delete(key); else query.set(key, value); }
    return `/archive/${target.replace("-", "/")}/${query.size ? `?${query}` : ""}`;
  };
  const cameras = filters.camera ? CAMERAS.filter((camera) => camera === filters.camera) : CAMERAS;
  const segments = filters.segment ? SEGMENTS.filter((segment) => segment === filters.segment) : SEGMENTS;
  const byKey = new Map((catalog?.items || []).map((video) => [`${video.cycle_date}/${video.camera}/${video.segment}`, video]));
  const count = catalog?.items.length ?? 0;
  // Days before the archive's first recording this month are blank, not "No recordings".
  const first = catalog?.items.reduce<string | null>((low, video) => (low === null || video.cycle_date < low ? video.cycle_date : low), null) ?? null;
  return <>
    <PageHead eyebrow="Timelapses · civil dawn to civil dawn" title={monthTitle(selectedMonth)} lede="Each day is one solar cycle: a daytime and an overnight recording from each of three cameras. Pick a day to see them side by side.">
      <Pager label="Archive months" previous={link({}, previous)} next={link({}, next)} previousLabel={monthTitle(previous)} nextLabel={monthTitle(next)} />
    </PageHead>
    <div className="actions" role="group" aria-label="Filters">
      <ul className="chips" aria-label="Camera"><li><Link to={link({ camera: null })} aria-current={!filters.camera ? "page" : undefined}>All cameras</Link></li>{CAMERAS.map((camera) => <li key={camera}><Link to={link({ camera })} aria-current={filters.camera === camera ? "page" : undefined}>{CAMERA_LABELS[camera]}</Link></li>)}</ul>
      <ul className="chips" aria-label="Segment"><li><Link to={link({ segment: null })} aria-current={!filters.segment ? "page" : undefined}>Both</Link></li>{SEGMENTS.map((segment) => <li key={segment}><Link to={link({ segment })} aria-current={filters.segment === segment ? "page" : undefined}>{SEGMENT_LABELS[segment]}</Link></li>)}</ul>
    </div>
    {!catalog && <p className="fault">Archive API unavailable. Which days have recordings cannot be shown; every day still opens.</p>}
    <h2 className="screen-reader">Solar-cycle calendar · {selectedMonth}</h2>
    <div className="calendar">
      {WEEKDAYS.map((day) => <span key={day} className="calendar-weekday" aria-hidden="true">{day}</span>)}
      {Array.from({ length: lead }, (_, index) => <span key={`blank-${index}`} className="calendar-blank" aria-hidden="true" />)}
      {Array.from({ length: days }, (_, index) => {
        const date = `${selectedMonth}-${String(index + 1).padStart(2, "0")}`;
        const present = cameras.flatMap((camera) => segments.map((segment) => byKey.get(`${date}/${camera}/${segment}`)));
        const has = present.some(Boolean);
        // The day's picture: the first filtered camera's daytime poster, else its overnight one (480 px only).
        const cover = segments.flatMap((segment) => cameras.map((camera) => byKey.get(`${date}/${camera}/${segment}`))).find((video) => video?.poster_small)?.poster_small;
        return <Link key={date} to={`/day/${date}/`} aria-label={`Open solar cycle ${date}`} data-has={catalog ? String(has) : undefined} data-cover={cover ? "true" : undefined} aria-current={date === today ? "date" : undefined}>
          {cover && <img className="calendar-cover" src={cover.url} alt="" width={cover.width ?? undefined} height={cover.height ?? undefined} loading="lazy" decoding="async" />}
          <span className="calendar-day">{index + 1}</span>
          {has ? <span className="state-dots" data-cols={cameras.length} aria-hidden="true">{segments.flatMap((segment) => cameras.map((camera) => { const video = byKey.get(`${date}/${camera}/${segment}`); return <span key={`${camera}-${segment}`} className="state-dot" data-state={video?.state ?? "none"} />; }))}</span> : <small>{date === today ? "Today · recording" : date > today || !first || date < first ? "" : "No recordings"}</small>}
        </Link>;
      })}
    </div>
    <ul className="legend" aria-hidden="true"><li><span className="state-dot" data-state="published" />Published</li><li><span className="state-dot" data-state="partial" />Partial</li><li><span className="state-dot" data-state="processing" />In progress</li><li><span className="state-dot" data-state="unavailable" />Unavailable</li><li>Rows: daytime, then overnight · columns: {cameras.map((camera) => CAMERA_LABELS[camera]).join(", ")}</li></ul>
    {catalog && <section className="section" aria-labelledby="recordings-title">
      <div className="section-head"><h2 id="recordings-title">Recordings this month</h2><span className="small">{count} {count === 1 ? "recording" : "recordings"}</span></div>
      {count === 0 ? <p className="muted">No recordings yet for these filters. Nothing has been replaced with a sample image.</p>
        : <details><summary>List all {count}</summary><ul className="rec-list">{catalog.items.map((video) => <li key={video.video_id}><VideoLink video={video} />{video.state !== "published" && <Status state={video.state} reason={video.reason} />}</li>)}</ul></details>}
    </section>}
  </>;
}
