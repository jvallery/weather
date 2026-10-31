# Weather ownership

What this repository owns and its state: **live** (running or applied), **disabled** (source in `deploy/k8s` but not rendered or at zero replicas) or **planned** (no source yet). Central record: agents `control-plane/agent-fleet-control/registry/policy/estate-ownership/`; update both in the same change set. Everything in namespace `weather` inherits Weather ownership except the agents-contributed objects listed below.

## Rendered by `deploy/k8s` (Argo Application `weather`)

| Object | Where | State |
|---|---|---|
| Namespace `weather` (PSA restricted), ResourceQuota `weather-budget`, LimitRange `weather-defaults` | `namespace.yaml`, `guardrails.yaml` | live |
| Tokenless ServiceAccounts `api`, `web`, `migrate`, `scheduler`, `intel`, `tempest-receiver`, `collector-<camera>` ×3, `pipeline-<camera>` ×3, `weather-preview` | `serviceaccounts.yaml` | live |
| NetworkPolicies `default-deny`, `api-ingress`, `api-garage-egress`, `web-ingress`, `web-egress` | `network-policy.yaml` | live |
| Deployments `api` ×2 and `web` ×2, their Services and PDBs; Service `api-operator` (the api's operator port 8001 only, #44) | `application.yaml` | live; `api-operator` source (#44) |
| IngressRoute `weather` (incl. `/media/` and `/downloads/` to api, `/api/v1/cameras/<camera>/preview` and `/media/previews/` to `weather-preview`), Middlewares `weather-rate-limit`, `weather-security-headers`, `weather-media-in-flight` (`weather.vallery.net`) | `ingress.yaml` | live |
| CNPG Cluster `weather-pg` (3 instances, PVCs `weather-pg-1..3`), ObjectStore `weather-pg-b2`, ScheduledBackup `weather-pg-daily`, ConfigMap `weather-database-policy`, NetworkPolicies `weather-pg`, `weather-database-clients`, `garage-change-gate-probe` (egress for the change-gate Garage probe Job only, storage-infra#1253); `Prune=false,Delete=false` | `database/` | live |
| CNPG-generated Secrets `weather-pg-ca`, `-server`, `-replication`, `-pull`, Roles/RoleBindings `weather-pg`, `weather-pg-barman-cloud`, PDBs `weather-pg`, `weather-pg-primary` | operator-owned children of `weather-pg` | live |
| Job `weather-migrate` (Argo Sync hook, wave 1) and NetworkPolicy `migrate` | `migrate/` | live |
| Schema: migrations 0001–0005 (telemetry, `weather_day`, job queue/outbox, publication catalog, search, events, `public_api` views and role grants) | `app/migrations/` | live |
| Media origin: `GET/HEAD` `/media/` and `/downloads/` in `api` with the `weather-media-s3-reader` mount (#20) | `app/weather/media/`; runbook [media](docs/operations/media.md#serve) | live |
| Events: scheduler job kinds `events` and `weather_event_changed`, `GET /api/v1/events[/{id}]`, web `/events/` (#41) | `app/weather/events/`; runbook [events](docs/operations/events.md) | live |
| Deployment `scheduler` ×1 and NetworkPolicy `scheduler` | `scheduler/` | live |
| Search: NetworkPolicy `api-cortex-egress`, IngressRoute `weather-search`, Middlewares `weather-search-rate-limit`, `weather-search-in-flight`, `weather-cortex` mount in `api` | `search/` component; runbook [search](docs/operations/search.md) | live (#25) |
| NetworkPolicy `pipeline` and Deployments `pipeline-skycam-east`, `pipeline-skycam-west`, `pipeline-frontyard-1` (×1 each; staging cleanup applying, `WEATHER_CLEANUP_ENABLED=true` (#150), own-camera staging subPaths; staging PVC, worker DB role, `weather-media-s3-writer`, `weather-originals-s3-writer`) | `pipeline/`; runbook [pipeline](docs/operations/pipeline.md) | live (#15, #19); #49 launches slice 2 |
| Schema `0006_render`: `solar_cycle`, `segment` (btree_gist overlap exclusion), worker read on `public_api.obs_st`/`rapid_wind` | `app/migrations/versions/0006_render.py`, `app/weather/pipeline/` | live (#15) |
| Schema `0009_stills`: `hour_slot`, `archival_still`, `still_derivative`, `public_api.stills`; the pipeline's hourly pass, `GET /api/v1/stills`, web `/stills/` (#38) | `app/migrations/versions/0009_stills.py`, `app/weather/stills/`, `app/weather/pipeline/hourly.py`; runbook [media](docs/operations/media.md#hourly-originals-and-comparison-stills) | source (#38); live with the pipelines at #49 |
| Schema `0010_cleanup`: `staging_chunk`, `staging_deletion`, `object_event`, `media_asset.state` `expired` and `state_changed_at` | `app/migrations/versions/0010_cleanup.py`, `app/weather/scheduler/cleanup.py`, `lifecycle.py` | live (#19) |
| Schema `0011_gc_kept`: `object_event.action` gains `gc_kept` | `app/migrations/versions/0011_gc_kept.py`, `app/weather/scheduler/lifecycle.py` | live (#19) |
| Schema `0008_keograms`: `keogram`, `keogram_bin`, `public_api.keograms`; `public_api.videos` replaced to report keogram status | `app/migrations/versions/0008_keograms.py`, `app/weather/pipeline/keogram*.py` | live (#37) |
| Route `/api/v1/keograms`; keogram regions `app/weather/pipeline/keogram-regions.json` (fixture values until #49) | `app/weather/api/keograms.py`, `app/weather/pipeline/` | source (#37) |
| Scheduler job kinds `segment.schedule`, `segment.finalize`, `segment.deadline`; optional `weather-site` mount in `scheduler` | `app/weather/pipeline/schedule.py`, `scheduler/` | source (#15); without the Secret schedule jobs fail visibly |
| Job `weather-pipeline-bench` (disposable throughput check) | `pipeline/bench-job.yaml`, never in a kustomization | applied by hand, then deleted |
| StatefulSets `collector-skycam-east`, `-skycam-west`, `-frontyard-1` ×1, ConfigMap `collector-capture-<hash>`, NetworkPolicy `collector`, PVC `weather-staging` (binds the storage-infra PV; `Prune=false,Delete=false`) | `capture/`; runbook [capture](docs/operations/capture.md) | live (#4, 0.5 Hz epoch) |
| Deployment `tempest-receiver` ×1 (Recreate, node6/node7), ConfigMap `tempest-receiver`, PVC `tempest-receiver-spool` (1 GiB `longhorn-v1-rwo`, `Prune=false,Delete=false`), NetworkPolicy `tempest-receiver` | `receiver/`; runbook [tempest-ingest](docs/operations/tempest-ingest.md) | live (#5) |
| Deployment `weather-preview` ×1 (staging `previews/` read-only, `weather-pg-api`), Service and NetworkPolicy `weather-preview` | `preview/`; runbook [media](docs/operations/media.md#previews) | live (#39) |
| Schema `0007_previews`: `camera_preview`, security-barrier `public_api.previews` (API role), worker insert/delete | `app/migrations/versions/0007_previews.py` | live (#39) |
| Preview producer in each `pipeline-<camera>`; files `previews/<camera>/` on the staging share (about 1 h) | `app/weather/preview/` | source (#39); runs when #49 enables the pipelines |
| Deployment `intel` ×1 (Recreate; worker DB role, `weather-cortex`, `weather-media-s3-reader`) and NetworkPolicy `intel` (Cortex and Garage via traefik-apps only) | `intel/`; runbook [intelligence](docs/operations/intelligence.md) | disabled (#23); #50 enables |
| Schema `0012_analysis`: `analysis`, `analysis_revision` (append-only), `ai_raw_response` (24 h, worker only), view `public_api.video_analyses` | `app/migrations/versions/0012_analysis.py`, `app/weather/ai/` | live (#23); `intel` stays disabled until #50 |
| Editor (#44): IngressRoute `weather-editor` (`/editor`, `/api/v1/operator/*` behind `apps-strip-auth-headers` and `ak-forwardauth`), the api's operator listener (8001, `weather_editor` role, `weather-pg-editor` mount), web `/editor/`, scheduler job kind `editorial.changed` | `editor-ingress.yaml`, `application.yaml`, `app/weather/editorial/`; runbook [editor](docs/operations/editor.md) | live (#44, deploy #155) |
| Schema `0013_editorial`: `editorial_operator` (one-user allowlist), `editorial_subject`, `editorial_revision` and `editorial_audit` (append-only), views `weather.video_editorial_state` and `public_api.video_editorial`, `telemetry_receipt.inserted_at`, `weather_editor` grants | `app/migrations/versions/0013_editorial.py` | live (#44) |
| Schema `0014_editorial_poster`: `editorial_revision.poster_frame`, append-only `editorial_poster_applied` (worker inserts), worker read on the editorial head, editor read of `media_asset (id, sha256)`; the pipeline's human-poster sweep (`app/weather/pipeline/poster.py`) | `app/migrations/versions/0014_editorial_poster.py`; runbook [editor](docs/operations/editor.md#poster-selection-78) | source (#78); applied with the next app pin |
| Schema `0016_obs_import`: `import_batch`, `obs_import` (imported Tempest history from verdify-platform, migrator-only writes), views `public_api.obs_st_history`, `rapid_wind_history` (live plus imported, `cap_seconds`/`provenance`, live wins) and `imported_history`; import transform `weather.importer.verdify` and `scripts/import_verdify_history.sh` | `app/migrations/versions/0016_obs_import.py`, `app/weather/importer/`; runbook [telemetry](docs/operations/telemetry.md#imported-history) | live (#167) |
| Schema `0017_obs_import_rapid`: `obs_import.family` (`obs_st` or `rapid_wind`, unique per source, family and time), `public_api.rapid_wind_history` with imported rapid-wind samples | `app/migrations/versions/0017_obs_import_rapid.py`; runbook [telemetry](docs/operations/telemetry.md#imported-history) | live (#167) |
| Job kinds `analysis.describe`, `analysis.synthesize` (claimed by `intel`, queued by the scheduler's `media.changed` hook and intel's nightly retry); `search.embed` claimed by `intel` | `app/weather/ai/`, `app/weather/scheduler/handlers.py` | source (#23) |
| Job and NetworkPolicy `weather-intel-smoke` (disposable one-call Cortex check) | `intel/smoke-job.yaml`, never in a kustomization | applied by hand, then deleted |
| Images `weather-app`, `weather-web` (digests in `kustomization.yaml`) | `registry.vallery.net/jvallery/` | live |
| Images `weather-python-base`, `weather-node-base`, `weather-ci` | `registry.vallery.net/jvallery/` | live |

Disposable restore-drill resources (`weather-drill-*`) are created by `scripts/restore-drill.sh`, labelled disposable with an expiry, never added to Argo, and deleted after use ([database runbook](docs/operations/database.md)).

## Contributed into `weather` by agents (Argo Application `weather-secrets`, `secrets-ksops/weather`)

| Object | Used by | State |
|---|---|---|
| Secrets `weather-pg-api`, `-editor`, `-ingest`, `-migrator`, `-worker` (database roles) | DB clients | live |
| Secrets `weather-pg-backup-b2-writer`, `weather-pg-backup-b2-reader` | `weather-pg` backups, restore drills | live |
| Secret `weather-media-s3-reader` | api media origin, intel (#23) | live |
| Secrets `weather-media-s3-writer`, `weather-originals-s3-writer`, `weather-custody-restore-reader` | pipelines, custody drill (#77) | live |
| Secrets `weather-camera-skycam-east`, `-skycam-west`, `-frontyard-1` | collectors | live |
| Secret `weather-cortex` | api search, intel, `weather-intel-smoke` | live (unused until #25 and #50 enable them) |
| Secret `weather-api-cursor` | api | live |
| Secret `weather-tempest` (hub and device serials) | `tempest-receiver` | live |
| Secret `weather-site` (almanac sun/moon position) | api | planned |
| NetworkPolicies `collector-skycam-east-egress`, `collector-skycam-west-egress`, `collector-frontyard-1-egress` | collectors | live |
| NetworkAttachmentDefinition `weather-tempest-vlan10` (VLAN 10 /32, Argo Application `multus`) | `tempest-receiver` | live (agents#5368) |

Secret `zot-origin-cluster-pull` is reflected from `agent-fleet-runners` for image pulls.

## Owned elsewhere, consumed here

| Item | Owner | State |
|---|---|---|
| Fleet registry record `gh-1431367372` (`ci-only`), AppProject and Applications | agents | live |
| Garage buckets `weather-media`, `weather-originals` and custody mirrors (Weather owns their objects and catalog) | storage-infra#1107 | live |
| B2 bucket for CNPG archives | storage-infra#1112 | live |
| NFS PV `weather-staging` | storage-infra#1106 | live (bound to PVC `weather/weather-staging`) |
| Hostname record, `apps-strip-auth-headers`, `ak-forwardauth` | network-infra | live |
| Authentik application and provider `weather-editor` (Jason-only, MFA) | agents#5366 | live |
| Scrape, probes, alert rules, dashboards | monitoring-stack#1072 | live (slice 1) |
| Cameras (sentinel), Tempest hub (homeassistant), Cortex routes (cortex-ai-compute), vallery.net homepage (VerdifyConsultancy/wordpress) | — | — |
