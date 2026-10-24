# Weather ownership

What this repository owns, where each item lives, and its current state. Central record: agents `control-plane/agent-fleet-control/registry/policy/estate-ownership/`. Update both in the same change set.

State as of 2026-10-24: namespace guardrails are deployed at `bed09184608c947dc0b95a784d8ca7f2facde28f`; Argo is Synced + Healthy. The walking skeleton is live at verified app/web digests. [Live evidence](docs/operations/evidence/skeleton-live-20261024.json) records the promotion and current source readback; live Tempest data remains uncommissioned. Evidence: [baseline receipt](docs/operations/evidence/foundation-baseline-20261024.json).

| Item | Kind | Where | Delivered by | State |
|---|---|---|---|---|
| `jvallery/weather` | Repository (public, MIT) | GitHub | — | Fleet registry `gh-1431367372` enrolled; source and live onboarding tracked in agents#5366 |
| `weather` | Namespace (PSA restricted, quota, default-deny) | k3s `vallery` | WX-01 | Deployed; Active, PSA restricted |
| Quota `weather-budget`, LimitRange `weather-defaults`, default-deny and role policies, tokenless role ServiceAccounts | Namespace guardrails, no RBAC | `weather`, rendered by `deploy/k8s` | WX-01 | Deployed; 12 tokenless accounts and four policies; no application or ingress enabled |
| `weather` | Argo Application (source `deploy/k8s`) | agents catalog / AppProject `weather` | agents#5366, WX-01 | Namespace baseline Synced + Healthy; API2/web2 are available at verified pins; latest source38572ca is Synced + Healthy |
| `weather.vallery.net` | Hostname (backend + route owner) | IngressRoutes in `weather` via wildcard tunnel | WX-01, network-infra#775 | Point-in-time public root/API health200 with HSTS and metrics404; intermittent edge404 investigation remains open |
| `weather-app`, `weather-web` | Images, digest-pinned | `registry.vallery.net/jvallery/` | WX-02, WX-01 | Built in-cluster and digest/config verified; API image FFmpeg9.0.2/libx264 and non-root runtime probe passed (evidence in docs/operations/evidence) |
| `weather-python-base`, `weather-node-base` | Upstream runtime mirrors, imported in-cluster and digest-pinned | `registry.vallery.net/jvallery/` | WX-02, agents#5366 | Imported via fixed fleet profiles; both manifests/configs SHA-verified as Linux amd64 at the pinned runtime versions (see docs/operations/base-imports.json) |
| `weather-pg` | CNPG Cluster, 3 instances, `longhorn-v1-rwo`, 20Gi each | `weather` | WX-05 | Source prepared under `deploy/k8s/database/`, excluded until prerequisites close; not deployed |
| CNPG archive | `weather-pg-b2` ObjectStore + `weather-pg-daily` ScheduledBackup; direct B2 | `weather`, B2 bucket owned by storage-infra | storage-infra#1112, WX-05 | Provider/key capability custody verified; application backup/PITR not commissioned |
| `weather-media`, `weather-originals` | Garage buckets on garage-hdd + custody mirror | storage-infra pipeline | storage-infra#1107 | Planned; Weather owns their objects and catalog (#18, `docs/operations/media.md`) |
| `weather-staging` | Static NFS PV/PVC (Obsidian share, quota) | storage-infra grants / `weather` | storage-infra#1106, WX-03 | Planned |
| Tempest receiver attachment | NetworkAttachmentDefinition (agents-owned contribution, VLAN 10 `vlan` plugin) + reserved IP | agents Multus / network-infra IPAM | agents#5368, network-infra#774, WX-04 | Planned |
| api, web | Workloads (2 replicas each) | `weather` | WX-01 | Running; point-in-time public/metrics/isolation and tokenless checks passed; continuous edge reliability pending; no station data yet |
| Collectors ×3, receiver, pipelines ×3, scheduler, intel | Workloads | `weather` | Slice issues | Planned |
| Weather monitoring | Scrape, probes, rules, dashboards | monitoring-stack | monitoring-stack#1072 + slice launches | Planned |
| Secrets (camera, B2, Garage, Cortex, DB) | KSOPS | agents `secrets-ksops/weather` | owner issues above | Planned |

Adjacent items this repo consumes but does not own: Tempest hub (homeassistant), cameras (sentinel), Cortex routes (cortex-ai-compute), `ak-forwardauth` (network-infra), vallery.net homepage (VerdifyConsultancy/wordpress).

## Snapshot collector source (WX-10)

`app/weather/capture` owns the isolated channel-1 snapshot client, absolute timer,
SOI/EOI, SOF-geometry, size and content-type checks, single-USTAR writer/reader and
capture metrics. Its private manifest v2 is defined by the Pydantic records in
`records.py` and follows the simplified P2 contract; there is no coordinator, writer
fencing or database dependency in this role.
Source preparation and small pure checks do not commission cameras or storage.
Real workloads, private egress rules and cadence remain WX-03; fake-camera
latency/response integration runs only in fleet CI. See [capture operations](docs/operations/capture.md).

Weather also owns the planned `weather/garage-probe-egress` NetworkPolicy in
`deploy/k8s/commissioning/garage-probe-egress.yaml`. It is excluded from the
foundation kustomization and has not been applied. It selects only the Garage
actuator's labelled commissioning probes; usage and removal are documented in
`docs/operations/garage-commissioning.md`.

Weather owns the excluded, transient NetworkPolicy
`weather/weather-custody-probe-egress` for storage-infra#1107's independent
B2-reader proof. It selects only labelled disposable operation pods, permits
kube-dns and TCP 443 for dynamic B2, and adds no ServiceAccount token, TCP
6443 exception, proxy, credentials or workload activation. Source: `deploy/k8s/commissioning/custody-probe-egress.yaml`;
bounded lifecycle: `docs/operations/garage-commissioning.md`.

## Schema and durable work (WX-08)

| Item | Location | State / activation |
|---|---|---|
| Slice-1 schema: station/device, receipts and telemetry families, `weather_day`, job/outbox, `public_api.observations`, role grants | `app/migrations/versions/0001_baseline.py`; current `app/weather/db/models.py` | Baseline (#74); applied to `weather-pg` by the deploy PR that pins its app digest. Later slices add their own revisions |
| API views `public_api.obs_st`, `public_api.rapid_wind`, `public_api.station` and their `weather_api` grants | `app/migrations/versions/0002_api.py` | #14; applied by the wave-1 migrate hook when the app digest that contains it is pinned |
| API database wiring: `database-role=api` label, `weather-pg-api` + `weather-pg-ca` mount, `weather-api-cursor` (`CURSOR_HMAC_KEY`) mount, sync-wave 2 | `deploy/k8s/application.yaml` | #14; runbook `docs/operations/api.md` |
| Almanac site mount: optional Secret `weather-site` (key `site.json`, private position for derived sun/moon times; never served) | `deploy/k8s/application.yaml` | #42; the Secret is agents KSOPS-owned and not yet created, so astronomy reports `site_not_configured`; runbook `docs/operations/api.md#almanac` |
| Search tables `embedding_profile`, `search_document`, `search_embedding`, views `public_api.search_documents`, `public_api.search_vectors`, function `public_api.search_lexical` and their worker/api grants | `app/migrations/versions/0004_search.py` | #25; applied by the migrate hook when the app digest that contains it is pinned. Runbook `docs/operations/search.md` |
| Search wiring: `weather-cortex` read-only mount and Cortex URL in `api`, NetworkPolicy `api-cortex-egress`, IngressRoute `weather-search`, Middlewares `weather-search-rate-limit` and `weather-search-in-flight` | kustomize component `deploy/k8s/search` | #25; disabled until the deploy commit that pins the app digest enables the component |
| `weather-migrate` Sync Job and database-only NetworkPolicy | `deploy/k8s/migrate` | Wave-1 Sync hook; excluded from root kustomization until the #74 deploy PR |
| `scheduler` Deployment and database/metrics NetworkPolicy | `deploy/k8s/scheduler` | One replica at wave 2 with the periodic tick; excluded until the #74 deploy PR |
| Publication catalog: `upload_intent`, `media_asset`, `publication`, `publication_asset`, `video`, `withdrawal`, `product_tombstone`, sequence `publication_fence`, views `public_api.assets` and `public_api.videos` | `app/migrations/versions/0003_publication.py`; `app/weather/publish/` | Source only (#18); applied by the deploy PR that pins its app digest. Runbook: `docs/operations/media.md` |
| Media origin: `/media/` and `/downloads/` IngressRoute entry and Middleware `weather-media-in-flight`, NetworkPolicy `api-garage-egress`, optional `weather-media-s3-reader` mount and `GARAGE_*` env on `api` | `deploy/k8s/ingress.yaml`, `deploy/k8s/network-policy.yaml`, `deploy/k8s/application.yaml`; code `app/weather/media/` | #20; live when the app digest that contains it is pinned. The Secret is agents KSOPS-delivered (storage-infra#1107). Runbook: `docs/operations/media.md#serve` |
| `weather/pipeline` NetworkPolicy (DNS, CNPG, Garage via traefik-apps, metrics ingress) | `deploy/k8s/pipeline` | Excluded from the root kustomization until the pipeline Deployments (#15) and bucket keys (storage-infra#1107) exist |
| `weather-ci` tooling image | `Dockerfile.ci`, `ci/browser/`, registry-owned repo-build profile | PG16.15/vector0.8.6 disposable integration and pinned Playwright browser tooling; never an application database |

These namespace-rendered application objects inherit the central `namespace:weather`
ownership declaration. Fleet validation/profile declarations and the CI image digest
pin are agents-owned; source/tests, schema and runtime image contents remain here.
Database objects rendered only when #6 commissions `deploy/k8s/database/`:
`NetworkPolicy/weather-pg`, `NetworkPolicy/weather-database-clients`, and
`ConfigMap/weather-database-policy` accompany the Cluster/ObjectStore/backup.
Their state is source prepared, not deployed. Production Cluster, ObjectStore,
namespace and data PVC protection remain in force. Reader-only `weather-drill-*`
Cluster/ObjectStore/NetworkPolicy/PVC resources are temporary Weather-owned
restore artifacts inside `weather`, labelled disposable with an expiry; they
are never added to Argo. See `docs/operations/database.md` for the exact limits
and cleanup evidence. Central namespace ownership is supplied by agents#5366;
all application objects inherit Weather ownership, while central Secrets remain
agents contributions and provider B2 identities remain storage-infra's.

## Tempest ingestion source preparation

Weather owns the `tempest-receiver` role source, its private SQLite WAL/FULL receipt spool, the raw `telemetry_receipt` and typed family tables in `weather-pg`, and receiver metrics. This source preparation creates no active receiver, PVC, network attachment or credential; #5 commissions those objects after native prerequisites. See [Tempest operations](docs/operations/tempest-ingest.md).

## Current-weather embed (#45)

`/embed/weather/` (and its error responses) is the only Weather page a parent may frame, and only `https://vallery.net` may frame it: the response CSP carries `frame-ancestors https://vallery.net` and no X-Frame-Options. Every other route keeps `frame-ancestors 'none'` and `X-Frame-Options: DENY`. Source: `web/app/routes/embed.weather.tsx`, `web/security-headers.mjs`; runbook [embed](docs/operations/embed.md). WordPress owns the parent CSP and homepage block (VerdifyConsultancy/wordpress#242).
