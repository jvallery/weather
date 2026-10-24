// SPDX-License-Identifier: MIT
import { useLoaderData, type LoaderFunctionArgs } from "react-router";
import { CameraPlaceholders } from "../components/camera-placeholders";
import { CurrentConditions } from "../components/current-conditions";
import { UnitForm } from "../components/common";
import { currentRead } from "../lib/public-api.server";
import { unitsFrom } from "../lib/route-params";
export const meta = () => [{ title: "Today · Weather" }];
export async function loader({ request }: LoaderFunctionArgs) { return { ...await currentRead(request), units: unitsFrom(new URL(request.url)) }; }
export default function Today() {
  const { current, now, units } = useLoaderData<typeof loader>();
  return <><h1>Today</h1><p>Local observations · America/Denver · source age remains visible.</p><UnitForm units={units} /><CurrentConditions initial={current} renderedAt={now} units={units} /><CameraPlaceholders /></>;
}
