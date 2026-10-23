# Weather

Requirements and implementation planning for **[weather.vallery.net](https://weather.vallery.net)**: a public weather history and timelapse service using three security cameras, direct Tempest UDP telemetry, CNPG, Garage, and local inference.

**Status:** planning audit in progress under [WX-00 / issue #1](https://github.com/jvallery/weather/issues/1). All 46 implementation/follow-on issues are [filed](docs/implementation/backlog.md), and their 122 native prerequisite relationships (including the planning gate and 14 owner issues) have been read back and matched through the initial full snapshot and subsequent exact changed-edge checks. See the [adversarial audit](docs/research/adversarial-audit.md), [decision register](docs/decisions/implementation-register.md) and [traceability](docs/implementation/traceability.md). Material research and contract work remain; application implementation, cluster resources and CI/CD have not been built.

## Start here

1. Read [the v4.0 requirements and design research brief](docs/requirements.md) and the [accepted product decisions](docs/decisions/2026-10-23-product-scope.md).
2. Follow [Codex's first assignment](docs/codex-first-task.md): investigate the live estate, resolve the decisions, complete repository configuration, then file the implementation issues and their dependency graph.
3. Use the [filed backlog](docs/implementation/backlog.md) and [machine-readable graph](docs/implementation/backlog.json). The [candidate graph](docs/implementation/backlog-candidate.json) is retained as the historical seed. Stable WX keys are distinct from GitHub issue numbers: 42 required initial-release items, two conditional GPU/CDN items, an accepted composite-video follow-on and a reserved visual-index investigation.

## Shared implementation contracts

- [Publication, source pins, origin eligibility and joint recovery](docs/implementation/publication-recovery-contract.md).
- [Database constraints, revision boundaries and migration sequence](docs/implementation/database-migration-contract.md).
- [Owner prerequisites and admission boundaries](docs/implementation/owner-prerequisites.md).
- [Estate standards applicability and evidence](docs/implementation/standards-mapping.md), with a [machine-readable matrix](docs/implementation/standards-mapping.json).
- [D04 direct Tempest attachment design](docs/decisions/D04-tempest-network.md), based on bounded live packet evidence.
- [D02 camera sources and source time](docs/decisions/D02-camera-sources-and-time.md), with three decoded native-endpoint samples and explicit cadence/privacy limits.
- [D05 receipt durability, canonical measurements and coverage](docs/decisions/D05-telemetry-integrity.md), with [adversarial telemetry vectors](docs/contracts/telemetry-vectors.json).
- [D06 dedicated PostgreSQL and storage admission](docs/decisions/D06-database-and-storage.md), with live extension/replication evidence and explicit storage prerequisites.
- [D09 CPU baseline and GPU admission](docs/decisions/D09-gpu-and-cpu-admission.md), with a verified nine-device census and explicit sharing limitations.
- [D08 role-scoped object gateway](docs/decisions/D08-object-access-boundary.md), including the action matrix and independent archive/recovery boundary.
- [D14 public media transport](docs/decisions/D14-public-media-transport.md), with live zone/tunnel evidence and an explicit unresolved external-route decision.
- [D15 custody/recovery design and open decisions](docs/decisions/D15-custody-and-recovery.md), grounded in [live central custody evidence](docs/research/custody-evidence.md).
- [HTTP/API semantic contract](docs/contracts/api-contract.md), [OpenAPI](docs/contracts/openapi.json), [JSON Schema](docs/contracts/api.schema.json), and [synthetic schema fixtures](docs/contracts/schema-fixtures.json).
- [Capture, chunk sealing and source-time assignment](docs/contracts/capture-contract.md), with [private capture schemas](docs/contracts/capture.schema.json).
- [Private recovery candidates and verification receipts](docs/contracts/recovery-contract.md), with [recovery schemas](docs/contracts/recovery.schema.json), [canonical byte rules](docs/contracts/recovery-canonicalization.md), [golden vectors](docs/contracts/recovery-canonical-vectors.json) and a separate [recovery validation receipt](docs/contracts/recovery-validation-receipt.json).
- [Private visual-model candidate schema](docs/contracts/visual-analysis.schema.json) and its [shape fixtures](docs/contracts/visual-analysis-fixtures.json); grounding remains a separate validation boundary.
- [Production acceptance evidence matrix](docs/implementation/acceptance-matrix.md), [hash-bound contract validation](docs/contracts/validation-receipt.json), and [historical full dependency readback](docs/implementation/graph-verification.json) (later owner-edge checks are recorded in the current backlog).

These are design contracts with mandatory implementation tests, not completed runtime qualification. The API/schema draft is published; remaining internal schemas, full contract coverage and design qualification are still in progress.

## Open-source comparison and product choices

The [open-source landscape review](docs/research/open-source-landscape.md) compares camera/timelapse applications, local weather collectors, public dashboards, media galleries and semantic search. It preserves source-level findings and the original questionnaire. **All 16 product questions are answered** in the [accepted decision record](docs/decisions/2026-10-23-product-scope.md) and incorporated into v4.0.

The selected direction is an independent, fleet-first application with documented customization. [Requirements §20.1](docs/requirements.md#201-selected-implementation-references-without-an-application-fork) maps reviewed solutions to implementation work and constraints. No application fork or mandatory fork experiment is selected.

## Required product

- Daytime and overnight timelapses for `skycam_east`, `skycam_west`, and `frontyard_1`, partitioned at civil twilight with no overlapping source intervals.
- A combined 120-second budget per camera/cycle, 60 fps, native downloads, browser-friendly renditions, and real-frame posters.
- Daytime and overnight keograms for all three cameras; per-camera video linked to weather plots, event bookmarks and an in-site feed.
- Timer-driven camera snapshots staged on an Obsidian-backed NFS PVC, then purged after required source-dependent work and qualified durable publication.
- One private native still per camera per actual local hour, archived in Garage with database/day foreign keys and sanitized comparison derivatives; selected five-year retention default.
- Delayed current previews for all three cameras, about one-minute refresh with a minimum ten-minute publication age.
- Direct local Tempest UDP collection preserving all received data, historical queries and current conditions in HA CNPG.
- Approximately 15-second current-weather refresh; local records, precipitation calendar, wind rose, sun/moon context and historical comparisons.
- One isolated private Garage bucket for media and database backups, with enforced application access boundaries and central B2 custody.
- Weather-focused analysis of completed video, versioned text embeddings, bounded hybrid search and typed measured-weather filters.
- Automatic labeled AI descriptions plus authenticated private corrections, tags, favorites and poster selection with provenance/reindexing.
- A responsive site consistent with `vallery.net`, an embeddable weather card, independent `weather` namespace and local CI/CD.

**Follow-on:** WX-45 tracks one combined east/west skycam video product while preserving the original camera outputs. WX-46 reserves visual-index research. Neither blocks the initial release. External forecasts/radar/alerts, product notifications, additional event clips, kiosk/PWA modes and multi-tenancy are outside the selected initial scope.

## Planning rules

The requirements distinguish fixed choices from decisions that need cluster investigation. GPU co-tenancy, UDP networking, CNPG/time-series extensions, Garage authorization and durability, framework choice, CDN delivery, and recovery objectives must be resolved with evidence.

This public repository contains fleet-focused code with documented customization and sanitized examples. Secrets, private topology, precise location, device identities and raw operational receipts belong in the existing private deployment/evidence system. Do not copy private reference repositories wholesale.

Jason selected [MIT](LICENSE) with [permissive selective reuse](docs/decisions/license-and-reuse.md) on 2026-10-23. Contribution/security guidance is recorded; CI and deployment remain implementation work. The selected hourly five-year default and preview retention still require explicit operational lifecycle/recovery configuration before deletion jobs are enabled.
