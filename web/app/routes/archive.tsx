// SPDX-License-Identifier: MIT
import { Form, Link, useLoaderData, type LoaderFunctionArgs } from "react-router";
import { Status, VideoLink } from "../components/common";
import { CAMERA_LABELS, localDate } from "../lib/format";
import { catalogRead } from "../lib/public-api.server";
import { catalogParams } from "../lib/route-params";
export const meta = () => [{ title: "Archive · Weather" }];
export async function loader({ request, params }: LoaderFunctionArgs) {
  const url = new URL(request.url);
  const selectedMonth = url.searchParams.get("month") || (params.year && params.month ? `${params.year}-${params.month}` : localDate(new Date().toISOString()).slice(0, 7));
  if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(selectedMonth)) throw new Response("Invalid archive month", { status: 400 });
  const filters = catalogParams(url);
  return { catalog: await catalogRead(request, filters), selectedMonth, filters: Object.fromEntries(filters) };
}
export default function Archive() {
  const { catalog, selectedMonth, filters } = useLoaderData<typeof loader>();
  const [year, month] = selectedMonth.split("-").map(Number);
  const days = new Date(Date.UTC(year, month, 0)).getUTCDate();
  const previous = new Date(Date.UTC(year, month - 2, 1)).toISOString().slice(0, 7);
  const next = new Date(Date.UTC(year, month, 1)).toISOString().slice(0, 7);
  const monthLink = (month: string) => {
    const query = new URLSearchParams(filters); query.delete("cursor");
    return `/archive/${month.replace("-", "/")}/${query.size ? `?${query}` : ""}`;
  };
  const pageLink = new URLSearchParams(filters);
  if (catalog?.next_cursor) pageLink.set("cursor", catalog.next_cursor);
  pageLink.set("month", selectedMonth);
  return <><h1>Explore the archive</h1><p>Browse solar cycles. Overnight recordings show both calendar dates.</p>
    <Form key={JSON.stringify([selectedMonth, filters.camera, filters.segment, filters.state])} method="get" className="filters"><label>Calendar month<input name="month" type="month" defaultValue={selectedMonth} required /></label><label>Camera<select name="camera" defaultValue={filters.camera || ""}><option value="">All cameras</option>{Object.entries(CAMERA_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><label>Segment<select name="segment" defaultValue={filters.segment || ""}><option value="">Both segments</option><option value="daytime">Daytime</option><option value="overnight">Overnight</option></select></label><label>State<select name="state" defaultValue={filters.state || ""}><option value="">All states</option>{["capturing", "queued", "processing", "published", "partial", "unavailable", "expired"].map((state) => <option value={state} key={state}>{state}</option>)}</select></label><button type="submit">Apply filters</button></Form>
    <nav aria-label="Archive months" className="actions"><Link className="button-link" to={monthLink(previous)}>Previous month · {previous}</Link><Link className="button-link" to={monthLink(next)}>Next month · {next}</Link></nav>
    <h2>Solar-cycle calendar · {selectedMonth}</h2><div className="calendar">{Array.from({ length: days }, (_, index) => {
      const date = `${selectedMonth}-${String(index + 1).padStart(2, "0")}`;
      return <Link key={date} to={`/day/${date}/`} aria-label={`Open solar cycle ${date}`}><span>{index + 1}</span><small>Open day</small></Link>;
    })}</div>
    <h2>Recording catalog</h2><p className="small">The calendar selects a day. The bounded catalog below uses the camera, segment and state filters; continue through pages for older products.</p>
    {!catalog ? <p className="fault">Archive API unavailable. Camera products are coming soon.</p> : catalog.items.length === 0 ? <p>No recordings yet for these filters. Nothing has been replaced with a sample image.</p> : <div className="grid two">{catalog.items.map((video) => <article className="card" key={video.video_id}><h3><VideoLink video={video} /></h3><Status state={video.state} reason={video.reason} /><p>Solar cycle · {video.cycle_date}</p><Link to={`/day/${video.cycle_date}/`}>Open this day and all three cameras</Link></article>)}</div>}
    {catalog?.next_cursor && <Link className="button-link" to={`?${pageLink}`}>Next catalog page</Link>}
  </>;
}
