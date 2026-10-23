# Weather

Requirements and implementation planning for **[weather.vallery.net](https://weather.vallery.net)**: a public weather history and timelapse service using three security cameras, direct Tempest UDP telemetry, CNPG, Garage, and local inference.

**Status:** requirements handoff. The application, cluster resources, CI/CD pipeline, and implementation issue backlog have not been built by this documentation publication.

## Start here

1. Read [the requirements and design research brief](docs/requirements.md).
2. Follow [Codex's first assignment](docs/codex-first-task.md): investigate the live estate, resolve the decisions, complete repository configuration, then file the implementation issues and their dependency graph.
3. Use [the candidate issue graph](docs/implementation/backlog-candidate.json) as a seed. Its 35 stable keys are candidates, **not GitHub issue numbers**. Revise them after research and replace the seed with a read-back-verified filed graph.

## Required product

- Daytime and overnight timelapses for `skycam_east`, `skycam_west`, and `frontyard_1`, partitioned at civil twilight with no overlapping source intervals.
- A combined 120-second budget per camera/cycle, 60 fps, native downloads, browser-friendly renditions, and real-frame posters.
- Timer-driven camera snapshots staged on an Obsidian-backed NFS PVC, then purged after qualified durable publication.
- Direct local Tempest UDP collection preserving all received data, historical queries and current conditions in HA CNPG.
- One isolated private Garage bucket for media and database backups, with enforced application access boundaries and central B2 custody.
- Weather-focused analysis of completed video, versioned pgvector embeddings, and bounded semantic search.
- A responsive site consistent with `vallery.net`, independent `weather` namespace, and local CI/CD.

## Planning rules

The requirements distinguish fixed choices from decisions that need cluster investigation. GPU co-tenancy, UDP networking, CNPG/time-series extensions, Garage authorization and durability, framework choice, CDN delivery, and recovery objectives must be resolved with evidence.

This public repository contains portable code and sanitized documentation. Secrets, private topology, precise location, device identities and raw operational receipts belong in the existing private deployment/evidence system. Do not copy private reference repositories wholesale.

The first pass must select an explicit source license and configure the project's contribution/security and repository policies. Until then, do not assume a license that has not been added.
