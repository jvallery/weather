// SPDX-License-Identifier: MIT
import { index, route, type RouteConfig } from "@react-router/dev/routes";
export default [index("routes/today.tsx"), route("about", "routes/about.tsx"), route("archive", "routes/archive.tsx"), route("archive/:year/:month", "routes/archive.tsx", { id: "archive-month" }), route("day/:date", "routes/day.tsx"), route("timelapses/:camera/:date/:segment", "routes/video.tsx"), route("weather/history", "routes/history.tsx"), route("search", "routes/not-ready.tsx")] satisfies RouteConfig;
