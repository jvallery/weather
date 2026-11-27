# Weather

Source for **[weather.vallery.net](https://weather.vallery.net)**: a public weather history and timelapse service built from three cameras, direct Tempest UDP telemetry, CNPG PostgreSQL, Garage object storage and local inference. It is MIT licensed, fleet-first and has documented customization points.

The service is live and is deployed from `main` by the Argo CD Application `weather`. Work is tracked in this repository's GitHub issues.

## Start here

1. [AGENTS.md](AGENTS.md): how to build, test, contribute and release a change, and the hazards.
2. [docs/architecture.md](docs/architecture.md): components, storage and identities, time model, design rules and repository layout.
3. [docs/operations/](docs/operations): one runbook per component.
4. [docs/contracts/](docs/contracts): the API contracts and the golden vectors the tests load.

Background: [requirements](docs/requirements.md) (R01–R38, the original brief) and the [product scope decisions](docs/decisions/2026-10-23-product-scope.md) (Q01–Q16) record what the service is for; the other files in [docs/decisions/](docs/decisions) are design records for single choices. Wireframes are in [docs/design/wireframes/](docs/design/wireframes) and research references in [docs/research/](docs/research). Where any of these differs from the architecture document, the architecture document is right.

## Product

- **Timelapses.** Daytime and overnight timelapses for `skycam_east`, `skycam_west` and `frontyard_1`, split at civil twilight with half-open, non-overlapping intervals. The two segments of a cycle share exactly 7,200 frames at 60 fps (≤120 s per camera per cycle). Each video has a native download, web and mobile renditions, and real-frame posters.
- **Keograms.** Daytime and overnight keograms for all three cameras.
- **Hourly stills.** One private native still per camera per local hour, kept 5 years, with calendar-day FKs and sanitized public comparison stills.
- **Previews.** Delayed current previews for all three cameras: about one refresh a minute, with a minimum age of ten minutes.
- **Telemetry.** Complete received Tempest telemetry, current conditions refreshed about every 15 s, history, exports, and a local almanac (records, precipitation calendar, wind rose, sun/moon, comparisons).
- **Exploration.** Per-camera video linked to weather plots, event bookmarks, a daily strip and an in-site feed.
- **AI and search.** Labeled AI descriptions, hybrid semantic and typed-measured search, and a private editor (corrections, tags, favorites, poster choice).
- **Embed and site.** A current-weather card embedded on the vallery.net homepage. The site is responsive and accessible, matching the vallery.net design.

**Follow-on:** an east/west composite skycam video (#46). **Deferred:** visual-index research (#47). **Out of scope:** external forecasts, radar and alerts; notifications; kiosk and PWA modes; multi-tenancy; identifying people.

## Privacy

This repository is public. Secrets, exact coordinates, camera addresses, station serials, private topology and raw operational logs never enter Git or issues. Deployment secrets are kept encrypted outside this repository.
