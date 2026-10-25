# Weather ownership

What this repository owns and its state: **live** (running or applied), **disabled** (source in `deploy/k8s` but not rendered or at zero replicas) or **planned** (no source yet). Central record: agents `control-plane/agent-fleet-control/registry/policy/estate-ownership/`; update both in the same change set. Everything in namespace `weather` inherits Weather ownership except the agents-contributed objects listed below.

## Rendered by `deploy/k8s` (Argo Application `weather`)

| Object | Where | State |
|---|---|---|
| Namespace `weather` (PSA restricted), ResourceQuota `weather-budget`, LimitRange `weather-defaults` | `namespace.yaml`, `guardrails.yaml` | live |
| Tokenless ServiceAccounts `api`, `web`, `migrate`, `scheduler`, `intel`, `tempest-receiver`, `collector-<camera>` ×3, `pipeline-<camera>` ×3 | `serviceaccounts.yaml` | live |
| NetworkPolicies `default-deny`, `api-ingress`, `api-garage-egress`, `web-ingress`, `web-egress` | `network-policy.yaml` | live |
| Deployments `api` ×2 and `web` ×2, their Services and PDBs | `application.yaml` | live |
| IngressRoute `weather` (incl. `/media/` and `/downloads/` to api), Middlewares `weather-rate-limit`, `weather-security-headers`, `weather-media-in-flight` (`weather.vallery.net`) | `ingress.yaml` | live |
| CNPG Cluster `weather-pg` (3 instances, PVCs `weather-pg-1..3`), ObjectStore `weather-pg-b2`, ScheduledBackup `weather-pg-daily`, ConfigMap `weather-database-policy`, NetworkPolicies `weather-pg`, `weather-database-clients`; `Prune=false,Delete=false` | `database/` | live |
| CNPG-generated Secrets `weather-pg-ca`, `-server`, `-replication`, `-pull`, Roles/RoleBindings `weather-pg`, `weather-pg-barman-cloud`, PDBs `weather-pg`, `weather-pg-primary` | operator-owned children of `weather-pg` | live |
| Job `weather-migrate` (Argo Sync hook, wave 1) and NetworkPolicy `migrate` | `migrate/` | live |
| Schema: migrations 0001–0005 (telemetry, `weather_day`, job queue/outbox, publication catalog, search, events, `public_api` views and role grants) | `app/migrations/` | live |
| Media origin: `GET/HEAD` `/media/` and `/downloads/` in `api` with the `weather-media-s3-reader` mount (#20) | `app/weather/media/`; runbook [media](docs/operations/media.md#serve) | live |
| Events: scheduler job kinds `events` and `weather_event_changed`, `GET /api/v1/events[/{id}]`, web `/events/` (#41) | `app/weather/events/`; runbook [events](docs/operations/events.md) | live |
| Deployment `scheduler` ×1 and NetworkPolicy `scheduler` | `scheduler/` | live |
| Search: NetworkPolicy `api-cortex-egress`, IngressRoute `weather-search`, Middlewares `weather-search-rate-limit`, `weather-search-in-flight`, `weather-cortex` mount in `api` | `search/` component; runbook [search](docs/operations/search.md) | disabled (#25) |
| NetworkPolicy `pipeline` and Deployments `pipeline-skycam-east`, `pipeline-skycam-west`, `pipeline-frontyard-1` (`replicas: 0`; staging PVC, worker DB role, `weather-media-s3-writer`) | `pipeline/`; runbook [pipeline](docs/operations/pipeline.md) | disabled (#15); #49 enables |
| Schema `0006_render`: `solar_cycle`, `segment` (btree_gist overlap exclusion), worker read on `public_api.obs_st`/`rapid_wind` | `app/migrations/versions/0006_render.py`, `app/weather/pipeline/` | source (#15); applied with the next app pin |
| Scheduler job kinds `segment.schedule`, `segment.finalize`, `segment.deadline`; optional `weather-site` mount in `scheduler` | `app/weather/pipeline/schedule.py`, `scheduler/` | source (#15); without the Secret schedule jobs fail visibly |
| Job `weather-pipeline-bench` (disposable throughput check) | `pipeline/bench-job.yaml`, never in a kustomization | applied by hand, then deleted |
| StatefulSets `collector-skycam-east`, `-skycam-west`, `-frontyard-1` ×1, ConfigMap `collector-capture-<hash>`, NetworkPolicy `collector`, PVC `weather-staging` (binds the storage-infra PV; `Prune=false,Delete=false`) | `capture/`; runbook [capture](docs/operations/capture.md) | live (#4, 0.5 Hz epoch) |
| `tempest-receiver` and its spool PVC | — | planned (#5) |
| `weather-preview`, `intel` | — | planned (#39, #23) |
| Images `weather-app`, `weather-web` (digests in `kustomization.yaml`) | `registry.vallery.net/jvallery/` | live |
| Images `weather-python-base`, `weather-node-base`, `weather-ci` | `registry.vallery.net/jvallery/` | live |

Disposable restore-drill resources (`weather-drill-*`) are created by `scripts/restore-drill.sh`, labelled disposable with an expiry, never added to Argo, and deleted after use ([database runbook](docs/operations/database.md)).

## Contributed into `weather` by agents (Argo Application `weather-secrets`, `secrets-ksops/weather`)

| Object | Used by | State |
|---|---|---|
| Secrets `weather-pg-api`, `-editor`, `-ingest`, `-migrator`, `-worker` (database roles) | DB clients | live |
| Secrets `weather-pg-backup-b2-writer`, `weather-pg-backup-b2-reader` | `weather-pg` backups, restore drills | live |
| Secret `weather-media-s3-reader` | api media origin | live |
| Secrets `weather-media-s3-writer`, `weather-originals-s3-writer`, `weather-custody-restore-reader` | pipelines, custody drill (#77) | live |
| Secrets `weather-camera-skycam-east`, `-skycam-west`, `-frontyard-1` | collectors | live |
| Secret `weather-cortex` | api search, intel | live (unused until #25 is enabled) |
| Secret `weather-api-cursor` | api | live |
| Secret `weather-site` (almanac sun/moon position) | api | planned |
| NetworkPolicies `collector-skycam-east-egress`, `collector-skycam-west-egress`, `collector-frontyard-1-egress` | collectors | live |
| NetworkAttachmentDefinition for the Tempest receiver (VLAN 10) | `tempest-receiver` | planned (agents#5368) |

Secret `zot-origin-cluster-pull` is reflected from `agent-fleet-runners` for image pulls.

## Owned elsewhere, consumed here

| Item | Owner | State |
|---|---|---|
| Fleet registry record `gh-1431367372` (`ci-only`), AppProject and Applications | agents | live |
| Garage buckets `weather-media`, `weather-originals` and custody mirrors (Weather owns their objects and catalog) | storage-infra#1107 | live |
| B2 bucket for CNPG archives | storage-infra#1112 | live |
| NFS PV `weather-staging` | storage-infra#1106 | live (bound to PVC `weather/weather-staging`) |
| Hostname record, `ak-forwardauth` | network-infra | live |
| Scrape, probes, alert rules, dashboards | monitoring-stack#1072 | live (slice 1) |
| Cameras (sentinel), Tempest hub (homeassistant), Cortex routes (cortex-ai-compute), vallery.net homepage (VerdifyConsultancy/wordpress) | — | — |
