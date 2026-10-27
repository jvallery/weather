// SPDX-License-Identifier: MIT
import { Form, Link, useLoaderData, type LoaderFunctionArgs } from "react-router";
import { PageHead } from "../components/common";
import { HourLinks, STILLS_NOTE, StillCard, cameraLabel, slotLabel } from "../components/stills";
import { cameraSchema } from "../lib/contracts";
import { CAMERA_LABELS, dateShift, dayTitle, localDate } from "../lib/format";
import { stillsRead } from "../lib/public-api.server";
import { calendarDate } from "../lib/route-params";
export const meta = () => [{ title: "Hourly stills · Weather" }];
// The compare view shows one clock hour on the selected date and the six before it.
const COMPARE_DAYS = 7;
export async function loader({ request }: LoaderFunctionArgs) {
  const url = new URL(request.url);
  const camera = url.searchParams.get("camera") || "skycam_east";
  if (!cameraSchema.safeParse(camera).success) throw new Response("Invalid camera", { status: 400 });
  const date = calendarDate(url.searchParams.get("date") || localDate(new Date().toISOString()));
  const hourText = url.searchParams.get("hour");
  if (hourText !== null && !/^(?:[01]?\d|2[0-3])$/.test(hourText)) throw new Response("Invalid hour", { status: 400 });
  const hour = hourText === null ? null : Number(hourText);
  const params = new URLSearchParams({ camera });
  if (hour === null) params.set("date", date);
  else { params.set("from", dateShift(date, 1 - COMPARE_DAYS)); params.set("to", date); params.set("hour", String(hour)); }
  return { camera, date, hour, page: await stillsRead(request, params) };
}
export default function Stills() {
  const { camera, date, hour, page } = useLoaderData<typeof loader>();
  const label = cameraLabel(camera);
  const link = (changes: Record<string, string | number | null>) => {
    const query = new URLSearchParams({ camera, date, ...(hour === null ? {} : { hour: String(hour) }) });
    for (const [key, value] of Object.entries(changes)) { if (value === null) query.delete(key); else query.set(key, String(value)); }
    return `/stills/?${query}`;
  };
  const step = hour === null ? 1 : COMPARE_DAYS;
  return <><PageHead eyebrow={`Hourly stills · ${hour === null ? dayTitle(date) : `${String(hour).padStart(2, "0")}:00 across a week`}`} title={`Hourly stills · ${label}`} lede={STILLS_NOTE} />
    <Form key={JSON.stringify([camera, date, hour])} method="get" className="filters"><label>Camera<select name="camera" defaultValue={camera}>{Object.entries(CAMERA_LABELS).map(([value, name]) => <option key={value} value={value}>{name}</option>)}</select></label><label>Calendar day<input name="date" type="date" defaultValue={date} required /></label><label>Local clock hour<select name="hour" defaultValue={hour === null ? "" : String(hour)}><option value="">Whole day</option>{Array.from({ length: 24 }, (_, value) => <option key={value} value={value}>{String(value).padStart(2, "0")}:00</option>)}</select></label><button type="submit">Show stills</button></Form>
    <nav className="actions pager-row" aria-label={hour === null ? "Adjacent days" : "Adjacent weeks and hours"}>
      <Link className="button-link" to={link({ date: dateShift(date, -step) })}>{hour === null ? "Previous day" : "Previous week"}</Link>
      <Link className="button-link" to={link({ date: dateShift(date, step) })}>{hour === null ? "Next day" : "Next week"}</Link>
      {hour !== null && <><Link className="button-link" to={link({ hour: (hour + 23) % 24 })}>Previous hour</Link><Link className="button-link" to={link({ hour: (hour + 1) % 24 })}>Next hour</Link><Link className="button-link" to={link({ hour: null })}>Whole day {date}</Link></>}
      <Link className="button-link" to={`/day/${date}/`}>Solar cycle {date}</Link>
    </nav>
    {!page ? <p className="fault">Stills API unavailable. No images are shown in its place.</p> : <>
      <div className="section-head section"><h2>{hour === null ? `Calendar day ${date} · ${page.items.length} hour slots` : `${String(hour).padStart(2, "0")}:00 local · ${dateShift(date, 1 - COMPARE_DAYS)} to ${date}`}</h2></div>
      {hour === null && <HourLinks camera={camera} date={date} stills={page.items} />}
      {page.items.length === 0 ? <p>No hour slot matches: this clock hour does not exist on these dates.</p> : <div className="grid stills">{page.items.map((still) => <StillCard key={`${still.camera}-${still.slot_id}`} still={still} heading={hour === null ? slotLabel(still) : `${still.calendar_date} · ${slotLabel(still)}`} />)}</div>}
    </>}
  </>;
}
