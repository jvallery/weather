# Weather

Source for **[weather.vallery.net](https://weather.vallery.net)**: a public weather history and timelapse service built from three cameras, direct Tempest UDP telemetry, CNPG PostgreSQL, Garage object storage and local inference. It is MIT licensed, fleet-first and has documented customization points.

**Status (2026-10-24):** the walking skeleton is live; slice 1 is in progress. Claude took over from Codex after a full audit, and the [takeover re-plan](docs/decisions/2026-10-24-takeover-replan.md) records the findings, decisions C1–C16 and the critical path. Work is tracked in GitHub issues with native dependencies.

## Start here

1. [AGENTS.md](AGENTS.md): how to pick ready work, the definition of done and the hazards.
2. [docs/architecture.md](docs/architecture.md): components, storage and identities, time model, repository layout and delivery slices.
3. [Simplification record](docs/decisions/2026-10-24-simplification.md) and the [takeover re-plan](docs/decisions/2026-10-24-takeover-replan.md), which amends it: what changed on 2026-10-24 and why.
4. [Requirements v4.1](docs/requirements.md) (R01–R38) and the [accepted product decisions](docs/decisions/2026-10-23-product-scope.md) (Q01–Q16).

The authoritative set is the files above, plus:

- **Contracts and golden vectors:** [docs/contracts/](docs/contracts).
- **Wireframes:** [docs/design/wireframes/](docs/design/wireframes).
- **Research references:** [docs/research/](docs/research).

The ADRs in [docs/decisions/](docs/decisions) (D01–D16) are historical; the rules that still bind are copied into [architecture.md](docs/architecture.md#rules-carried-from-adrs). Planning-era audits, receipts and superseded contracts live in [docs/archive/planning-2026-10/](docs/archive/planning-2026-10). They are history, not instructions.

## Product (initial release)

- **Timelapses.** Daytime and overnight timelapses for `skycam_east`, `skycam_west` and `frontyard_1`, split at civil twilight with half-open, non-overlapping intervals. The two segments of a cycle share exactly 7,200 frames at 60 fps (≤120 s per camera per cycle). Each video has a native download, web and mobile renditions, and real-frame posters.
- **Keograms.** Daytime and overnight keograms for all three cameras.
- **Hourly stills.** One private native still per camera per local hour, kept 5 years, with calendar-day FKs and sanitized public comparison stills.
- **Previews.** Delayed current previews for all three cameras: about one refresh a minute, with a minimum age of ten minutes.
- **Telemetry.** Complete received Tempest telemetry, current conditions refreshed about every 15 s, history, exports, and a local almanac (records, precipitation calendar, wind rose, sun/moon, comparisons).
- **Exploration.** Per-camera video linked to weather plots, event bookmarks, a daily strip and an in-site feed.
- **AI and search.** Labeled AI descriptions, hybrid semantic and typed-measured search, and a private editor (corrections, tags, favorites, poster choice).
- **Embed and site.** A current-weather card embedded on the vallery.net homepage. The site is responsive and accessible, matching the vallery.net design.

**Follow-on:** an east/west composite skycam video (WX-45, #46). **Deferred:** visual-index research (WX-46, #47). **Out of scope:** external forecasts, radar and alerts; notifications; kiosk and PWA modes; multi-tenancy; identifying people.

## Delivery slices

| Slice | Goes public at | Contents |
|---|---|---|
| 0 Foundation | skeleton | scaffold, CI/CD, namespace, schema, time model, database and backups |
| 1 Live weather | [#48](https://github.com/jvallery/weather/issues/48) | Tempest ingest, current conditions, history, almanac, embed card |
| 2 Timelapse | [#49](https://github.com/jvallery/weather/issues/49) | capture, streaming render, publication, media origin, keograms, hourly stills, previews, linked plots |
| 3 Intelligence | [#50](https://github.com/jvallery/weather/issues/50) | AI descriptions, search, events, editor |
| Closeout | [#36](https://github.com/jvallery/weather/issues/36) | B2-only restore drill, homepage card, SLO evidence, open owner decisions |

## Privacy

This repository is public. Secrets, exact coordinates, camera addresses, station serials, private topology and raw operational receipts never enter Git or issues. Deployment secrets live in agents KSOPS (`secrets-ksops/weather`).
