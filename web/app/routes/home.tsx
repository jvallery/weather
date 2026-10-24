// SPDX-License-Identifier: MIT
type Health = { apiState: "healthy" | "unavailable" };

export async function loader(): Promise<Health> {
  try {
    const origin = process.env.API_ORIGIN ?? "http://api";
    const response = await fetch(`${origin}/healthz`, { signal: AbortSignal.timeout(3000) });
    const body: unknown = await response.json();
    return { apiState: response.ok && typeof body === "object" && body !== null &&
      "status" in body && body.status === "ok" ? "healthy" : "unavailable" };
  } catch {
    return { apiState: "unavailable" };
  }
}

export default function Home({ loaderData }: { loaderData?: Health }) {
  return <main>
    <h1>Weather</h1>
    <p>The weather archive is being commissioned.</p>
    <p role="status">{loaderData?.apiState === "healthy" ?
      "Weather API is healthy. Live conditions are coming soon." :
      "Weather API is unavailable. Commissioning is in progress."}</p>
  </main>;
}
