# Runbooks

Weather's runbooks live in [docs/operations/](../operations), one per component. Use this index to
find the right one.

| Runbook | Use it when |
|---|---|
| [deploy.md](../operations/deploy.md) | Checking a rollout, the capacity envelope, rolling back an image |
| [development.md](../operations/development.md) | Setting up toolchains, changing locks, the license inventory |
| [database.md](../operations/database.md) | Anything about `weather-pg`: roles, backups, restores, restore drills |
| [recovery.md](../operations/recovery.md) | Restoring media from its off-site copy, the media restore drill |
| [api.md](../operations/api.md) | The public API Deployment and its endpoints |
| [web.md](../operations/web.md) | The public web application |
| [capture.md](../operations/capture.md) | The snapshot collectors |
| [pipeline.md](../operations/pipeline.md) | The per-camera timelapse pipelines |
| [media.md](../operations/media.md) | Media publication, storage and retention |
| [tempest-ingest.md](../operations/tempest-ingest.md) | The Tempest receiver and its spool |
| [telemetry.md](../operations/telemetry.md) | An alert fired: each alert's signal and the runbook that owns the fix |
| [logging.md](../operations/logging.md) | Log format and what may be logged |
| [intelligence.md](../operations/intelligence.md) | AI descriptions |
| [search.md](../operations/search.md) | Search ranking and its index |
| [events.md](../operations/events.md) | Measured and editorial events |
| [editor.md](../operations/editor.md) | The private editor |
| [embed.md](../operations/embed.md) | The current-weather card embedded on the vallery.net homepage |
| [time-model.md](../operations/time-model.md) | The shared time model |
| [ffmpeg.md](../operations/ffmpeg.md) | The pinned FFmpeg build (an input of the `weather-app` image) |
| [public-launch.md](../operations/public-launch.md) | The read-only public edge smoke |
