// SPDX-License-Identifier: MIT
import { useLoaderData, type LoaderFunctionArgs } from "react-router";
import { EmbedWeatherCard } from "../components/embed-weather-card";
import { currentRead, metricRegistryRead } from "../lib/public-api.server";
import { unitsFrom } from "../lib/route-params";
// Framing (frame-ancestors https://vallery.net, no X-Frame-Options) is set by security-headers.mjs for this path.
export const meta = () => [{ title: "Current weather · Weather" }];
export async function loader({ request }: LoaderFunctionArgs) {
  const [registry, current] = await Promise.all([metricRegistryRead(request), currentRead(request)]);
  return { ...current, registry, units: unitsFrom(new URL(request.url)) };
}
export default function EmbedWeather() {
  const { current, now, units, registry, apiUnavailable } = useLoaderData<typeof loader>();
  return <EmbedWeatherCard initial={current} renderedAt={now} units={units} registry={registry} apiUnavailable={apiUnavailable} />;
}
