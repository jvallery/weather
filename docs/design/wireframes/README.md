# Responsive Weather wireframes

These are static design collateral for the owner-approved React/TypeScript frontend, not application source or a running product. All measurements, dates, evidence and imagery are synthetic. Controls illustrate layout; they do not call APIs or perform actions. The source is self-contained [HTML](index.html), with no JavaScript, external assets or camera data. System fonts stand in for the approved locally hosted IBM Plex fonts.

The design follows [D13](../../decisions/D13-ui-and-editor-boundaries.md): current conditions first, explicit source age, separate camera/video/analysis states, native panorama, linked evidence, calendar-day versus solar-cycle distinctions, private editor CAS and a compact public embed. The selection of React changes implementation ownership, not those product requirements.

## Route and artifact map

Each public §12.2 route group and the private editor has a desktop and narrow-mobile render. API/media routes are represented by their consuming view and retain their separate transport contracts; an API endpoint does not need an invented screen.

| View | Route/purpose | Desktop (1440 px) | Mobile (320 px) |
|---|---|---|---|
| Today | `/` | [Desktop](renders/today-1440.png) | [Mobile](renders/today-320.png) |
| Explore the archive | `/archive/ and month views` | [Desktop](renders/archive-1440.png) | [Mobile](renders/archive-320.png) |
| Solar cycle · October 23 | `/day/YYYY-MM-DD/` | [Desktop](renders/day-1440.png) | [Mobile](renders/day-320.png) |
| East sky · daytime | `/timelapses/<camera>/<date>/<segment>/` | [Desktop](renders/player-1440.png) | [Mobile](renders/player-320.png) |
| Hourly observations | `/stills/ and camera/date views` | [Desktop](renders/stills-1440.png) | [Mobile](renders/stills-320.png) |
| Measured weather history | `/weather/history/` | [Desktop](renders/history-1440.png) | [Mobile](renders/history-320.png) |
| Local almanac | `/weather/almanac/` | [Desktop](renders/almanac-1440.png) | [Mobile](renders/almanac-320.png) |
| Weather events | `/events/` | [Desktop](renders/events-1440.png) | [Mobile](renders/events-320.png) |
| Find recorded weather | `/search/` | [Desktop](renders/search-1440.png) | [Mobile](renders/search-320.png) |
| Weather card | `/embed/weather/` | [Desktop](renders/embed-1440.png) | [Mobile](renders/embed-320.png) |
| About these observations | `/about/` | [Desktop](renders/about-1440.png) | [Mobile](renders/about-320.png) |
| Private editorial workspace | `Private editorial UI/API` | [Desktop](renders/editor-1440.png) | [Mobile](renders/editor-320.png) |
| Failure and lifecycle states | `Cross-route acceptance` | [Desktop](renders/states-1440.png) | [Mobile](renders/states-320.png) |

## What was checked

The [hash-bound render receipt](render-verification.json) records 52 section/viewport checks: all 13 views at 320, 390, 768 and 1440 CSS pixels. All fit their page width; tables may use their labeled local scroll container. Twenty-six PNGs preserve the two endpoint widths. One headless Chrome process rendered the self-contained HTML with external HTTP requests blocked; none were attempted. This was bounded local document rendering, not an application build or cluster workload.

Desktop/mobile contact sheets were visually inspected, with full-size Today and player inspection. The final refinement adds both per-camera segment keograms, separates current-cycle state from latest-product availability and cycle aggregates from current readings, gives unavailable/partial badges distinct treatment and removes full navigation from the embed view. The source, final PNGs and receipt must stay together; rerender after any visual source change.

## Acceptance still owned by implementation

WX-20 owns the shell/data lifecycle, archive/day/player basics and current polling. WX-26 owns search interactions; WX-39/40 own linked player/events; WX-41 owns almanac; WX-43 owns private editorial controls; WX-44 owns parent embed integration. Image/still/preview producers retain their privacy and publication contracts. WX-33 integrates all required routes and failure states.

These wireframes do not prove React hydration, API behavior, real plotting, play/seek synchronization, typography distribution, authentication, cache withdrawal, 200% zoom, keyboard/screen-reader behavior, browser/mobile compatibility or production performance. The mock wind rose, charts and image areas specify placement and labeling, not scientific visual correctness. The page skeleton and static text do not waive the substantive behavior in D13, the UI clause audit or requirements §12.2. Native video geometry, valid evidence seeks and complete graph/record semantics remain implementation obligations.

Long mobile pages intentionally preserve all camera/segment content in reading order. Implementation may add accessible jump links or progressive disclosure while keeping the same information reachable, labeled and usable without JavaScript where required. The embed's surrounding banner/title are design annotations; only the compact card is embedded in the parent site.
