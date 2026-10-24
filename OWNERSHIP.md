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
| `weather-pg` | CNPG Cluster, 3 instances, `longhorn-v1-rwo` | `weather` | WX-05 | Planned |
| CNPG archive | B2 bucket + ObjectStore + ScheduledBackup | B2 (storage-infra account) | storage-infra#1112, WX-05 | Planned |
| `weather-media`, `weather-originals` | Garage buckets on garage-hdd + custody mirror | storage-infra pipeline | storage-infra#1107 | Planned |
| `weather-staging` | Static NFS PV/PVC (Obsidian share, quota) | storage-infra grants / `weather` | storage-infra#1106, WX-03 | Planned |
| Tempest receiver attachment | NetworkAttachmentDefinition (agents-owned contribution, VLAN 10 `vlan` plugin) + reserved IP | agents Multus / network-infra IPAM | agents#5368, network-infra#774, WX-04 | Planned |
| api, web | Workloads (2 replicas each) | `weather` | WX-01 | Running; point-in-time public/metrics/isolation and tokenless checks passed; continuous edge reliability pending; no station data yet |
| Collectors ×3, receiver, pipelines ×3, scheduler, intel | Workloads | `weather` | Slice issues | Planned |
| Weather monitoring | Scrape, probes, rules, dashboards | monitoring-stack | monitoring-stack#1072 + slice launches | Planned |
| Secrets (camera, B2, Garage, Cortex, DB, API cursor) | KSOPS | agents `secrets-ksops/weather` | owner issues above | Planned |

Adjacent items this repo consumes but does not own: Tempest hub (homeassistant), cameras (sentinel), Cortex routes (cortex-ai-compute), `ak-forwardauth` (network-infra), vallery.net homepage (VerdifyConsultancy/wordpress).

## Current/publication database source (WX-13)

| Item | Source | State |
|---|---|---|
| `metric_binding`, `metric_current` | Immutable `db/schema_v3.py`, migration `0003_current_publication` | Source preparation; pointers/revisions only, safe values join v2 qualification |
| `telemetry_publication_clock`, `telemetry_publication` | Same migration and `db/ddl_v3.py` | Transactional export membership; private application writes denied |
| Safe current/publication views, scalar watermark and clock-first telemetry triggers | `public_api.metric_current`, `public_api.telemetry_publications`, `public_api.telemetry_snapshot_watermark()` | PostgreSQL integration unrun; no workload enabled |
| Read-only API connection, safe DTO mapping and SQL source | `db/public_connection.py`, `db/public_mapping.py`, `db/public_source.py` | Bounded verified-TLS source prep; 8 focused pure checks pass, 3 PostgreSQL reader cases unrun; no HTTP/cache/HMAC or deployment |
| Complete-window safe evidence reader | `db/public_evidence.py` | Explicit calibrated bookends and exact decimals; no authority/statistic math; cumulative 10 pure checks pass, 4 reader PostgreSQL cases unrun |

These are application-owned database objects inside Weather's existing namespace
ownership, not new infrastructure or access identities. Activation and remaining
model/test integration belong to #14 after #9/#12; see
[current/publication operations](docs/operations/current-publication.md).

## Snapshot collector source (WX-10)

`app/weather/capture` owns the isolated channel-1 snapshot client, absolute timer,
strict native-JPEG validation, single-USTAR writer/reader and capture metrics. Its
private manifest v2 is generated from code and follows the simplified P2 contract;
there is no coordinator, writer fencing or database dependency in this role.
Source preparation and small pure checks do not commission cameras or storage.
Real workloads, private egress rules and cadence remain WX-03; fake-camera
latency/decode integration runs only in fleet CI. See [capture operations](docs/operations/capture.md).

## Schema and durable work (WX-08)

| Item | Location | State / activation |
|---|---|---|
| Retained telemetry, calendar/capture/catalog/editorial/search/job/outbox relations | `app/weather/db/schema_v1.py`, current `models.metadata` | Schema v1; cluster qualification required before #9 closes; production application belongs to #6 |
| Public read projections, scoped grants and CAS/append-only guards | `app/weather/db/ddl_v1.py` | No raw receipts, private IDs, originals or private editorial tables granted to API |
| `weather-migrate` Sync Job and database-only NetworkPolicy | `deploy/k8s/migrate` | Excluded from root kustomization until #6 commissions the database |
| `scheduler` Deployment and database/metrics NetworkPolicy | `deploy/k8s/scheduler` | Zero replicas and excluded; #14 registers handlers and enables it |
| `weather-ci` tooling image | `Dockerfile.ci`, `ci/browser/`, registry-owned repo-build profile | PG16.15/vector0.8.6 disposable integration and pinned Playwright browser tooling; never an application database |

These namespace-rendered application objects inherit the central `namespace:weather`
ownership declaration. Fleet validation/profile declarations and the CI image digest
pin are agents-owned; source/tests, schema and runtime image contents remain here.

The WX-13 cursor integrity Secret `weather-api-cursor` (`CURSOR_HMAC_KEY`) is
prepared in agents PR #5469: generated once, SOPS sealed and independently
restore-read on the operator Mac. It is not mounted or enabled here; only API
pods consume it when WX-13 is commissioned. No credential value is stored in
this repository.

The opt-in API factory owns the startup policy/key readers, bounded background cache lifecycle,
public discovery cadence and read-only API pool wiring. It remains disabled by default and changes
no live mount, label, image pin or replica count. See [API runtime](docs/operations/api-runtime.md).

Weather also owns immutable safe telemetry qualification history (migration 0004), retained aggregate
revisions and fenced heads (0005), and their read-only public projections. These remain prepared
source until the WX-13 cluster/database qualification and disabled scheduler commissioning complete.

The excluded `deploy/k8s/api-data` component prepares the namespace-owned public policy ConfigMap,
API-only credential mounts and singleton scheduler activation. Its public policy files contain no
station identity or location coordinates. The root render does not enable this component.

## Tempest ingestion source preparation

Weather owns the `tempest-receiver` role source, its private SQLite WAL/FULL receipt spool, additive telemetry provenance/conflict/coverage schema and receiver metrics. Migration `0002_telemetry` extends schema v1 without changing its snapshot. This source preparation creates no active receiver, PVC, network attachment or credential; #5 commissions those objects after native prerequisites. See [Tempest operations](docs/operations/tempest-ingest.md).
