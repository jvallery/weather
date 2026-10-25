// SPDX-License-Identifier: MIT
import { Link, useLoaderData, type LoaderFunctionArgs } from "react-router";
import { CameraTiles } from "../components/camera-tiles";
import { CurrentConditions } from "../components/current-conditions";
import { UnitForm } from "../components/common";
import { EventItem } from "../components/events";
import { catalogRead, currentRead, eventsRead, metricRegistryRead } from "../lib/public-api.server";
import { unitsFrom } from "../lib/route-params";
export const meta = () => [{ title: "Today · Weather" }];
export async function loader({ request }: LoaderFunctionArgs) {
  const [registry, current, events, media] = await Promise.all([metricRegistryRead(request), currentRead(request), eventsRead(request, new URLSearchParams({ order: "desc", limit: "5" })), catalogRead(request, new URLSearchParams({ limit: "24" }))]);
  return { ...current, registry, recent: events?.items || null, media: media?.items || null, units: unitsFrom(new URL(request.url)) };
}
export default function Today() {
  const { current, now, units, registry, apiUnavailable, recent, media } = useLoaderData<typeof loader>();
  return <><h1>Today</h1><p>Local observations · America/Denver · source age remains visible.</p><UnitForm units={units} /><CurrentConditions initial={current} renderedAt={now} units={units} registry={registry} apiUnavailable={apiUnavailable} />
    <section aria-labelledby="recent-events-title"><h2 id="recent-events-title">Recent weather events</h2>{recent === null ? <p className="fault">Events API unavailable. No events are shown in its place.</p> : recent.length === 0 ? <p>No weather events recorded yet.</p> : <ol className="events">{recent.map((event) => <EventItem key={event.id} event={event} />)}</ol>}<p><Link to="/events/">All weather events</Link></p></section>
    <CameraTiles media={media} /></>;
}
