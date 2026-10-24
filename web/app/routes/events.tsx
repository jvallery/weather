// SPDX-License-Identifier: MIT
import { Form, Link, useLoaderData, type LoaderFunctionArgs } from "react-router";
import { EventFeedItem } from "../components/events";
import { cameraSchema } from "../lib/contracts";
import { CAMERA_LABELS } from "../lib/format";
import { eventsRead } from "../lib/public-api.server";
import { calendarDate } from "../lib/route-params";
export const meta = () => [{ title: "Weather events · Weather" }];
const ORIGINS = { measured: "Measured", visual: "Visual (AI)", synthesis: "AI synthesis", manual: "Manual" } as const;
export async function loader({ request }: LoaderFunctionArgs) {
  const url = new URL(request.url);
  const filters = new URLSearchParams();
  const day = url.searchParams.get("day"), camera = url.searchParams.get("camera"), origin = url.searchParams.get("origin"), cursor = url.searchParams.get("cursor");
  if (day) filters.set("day", calendarDate(day));
  if (camera) { if (!cameraSchema.safeParse(camera).success) throw new Response("Invalid camera", { status: 400 }); filters.set("camera", camera); }
  if (origin) { if (!(origin in ORIGINS)) throw new Response("Invalid origin", { status: 400 }); filters.set("origin", origin); }
  const params = new URLSearchParams(filters);
  if (cursor) { if (cursor.length > 3000 || !/^[A-Za-z0-9_.-]+$/.test(cursor)) throw new Response("Invalid page", { status: 400 }); params.set("cursor", cursor); }
  params.set("limit", "50");
  return { feed: await eventsRead(request, params), filters: Object.fromEntries(filters) };
}
export default function EventsFeed() {
  const { feed, filters } = useLoaderData<typeof loader>();
  const next = new URLSearchParams(filters);
  if (feed?.next_cursor) next.set("cursor", feed.next_cursor);
  return <><h1>Weather events</h1><p>Measured events come from the station's instruments; visual and AI events are interpretations of recorded video; manual events are human annotations. Each shows its provenance. Times are source times, oldest first.</p>
    <Form key={JSON.stringify(filters)} method="get" className="filters"><label>Calendar day<input name="day" type="date" defaultValue={filters.day || ""} /></label><label>Camera<select name="camera" defaultValue={filters.camera || ""}><option value="">All cameras and station-wide</option>{Object.entries(CAMERA_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><label>Origin<select name="origin" defaultValue={filters.origin || ""}><option value="">All origins</option>{Object.entries(ORIGINS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><button type="submit">Apply filters</button></Form>
    <h2>Events</h2>
    {!feed ? <p className="fault">Events API unavailable. No events are shown in its place.</p> : feed.items.length === 0 ? <p>No weather events match these filters.</p> : <ol className="events">{feed.items.map((event) => <EventFeedItem key={event.id} event={event} />)}</ol>}
    {feed?.next_cursor && <Link className="button-link" to={`?${next}`}>Next events page</Link>}
  </>;
}
