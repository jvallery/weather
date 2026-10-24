# Weather ownership

What this repository owns, where each item lives, and its current state. Central record: agents `control-plane/agent-fleet-control/registry/policy/estate-ownership/`. Update both in the same change set.

State as of 2026-10-24: namespace guardrails are deployed at `bed09184608c947dc0b95a784d8ca7f2facde28f`; Argo is Synced + Healthy. The initial API/web digest promotion is defined below; live commissioning acceptance remains WX-01. Evidence: [baseline receipt](docs/operations/evidence/foundation-baseline-20261024.json).

| Item | Kind | Where | Delivered by | State |
|---|---|---|---|---|
| `jvallery/weather` | Repository (public, MIT) | GitHub | — | Fleet registry `gh-1431367372` enrolled; source and live onboarding tracked in agents#5366 |
| `weather` | Namespace (PSA restricted, quota, default-deny) | k3s `vallery` | WX-01 | Deployed; Active, PSA restricted |
| Quota `weather-budget`, LimitRange `weather-defaults`, default-deny and role policies, tokenless role ServiceAccounts | Namespace guardrails, no RBAC | `weather`, rendered by `deploy/k8s` | WX-01 | Deployed; 12 tokenless accounts and four policies; no application or ingress enabled |
| `weather` | Argo Application (source `deploy/k8s`) | agents catalog / AppProject `weather` | agents#5366, WX-01 | Namespace baseline Synced + Healthy; two API and two web replicas are defined at verified image pins; live promotion verification tracked in WX-01 |
| `weather.vallery.net` | Hostname (backend + route owner) | IngressRoutes in `weather` via wildcard tunnel | WX-01, network-infra#775 | Public read-only IngressRoute defined; live route verification tracked in WX-01 |
| `weather-app`, `weather-web` | Images, digest-pinned | `registry.vallery.net/jvallery/` | WX-02, WX-01 | Built in-cluster and digest/config verified; API image FFmpeg9.0.2/libx264 and non-root runtime probe passed (evidence in docs/operations/evidence) |
| `weather-python-base`, `weather-node-base` | Upstream runtime mirrors, imported in-cluster and digest-pinned | `registry.vallery.net/jvallery/` | WX-02, agents#5366 | Imported via fixed fleet profiles; both manifests/configs SHA-verified as Linux amd64 at the pinned runtime versions (see docs/operations/base-imports.json) |
| `weather-pg` | CNPG Cluster, 3 instances, `longhorn-v1-rwo` | `weather` | WX-05 | Planned |
| CNPG archive | B2 bucket + ObjectStore + ScheduledBackup | B2 (storage-infra account) | storage-infra#1112, WX-05 | Planned |
| `weather-media`, `weather-originals` | Garage buckets on garage-hdd + custody mirror | storage-infra pipeline | storage-infra#1107 | Planned |
| `weather-staging` | Static NFS PV/PVC (Obsidian share, quota) | storage-infra grants / `weather` | storage-infra#1106, WX-03 | Planned |
| Tempest receiver attachment | NetworkAttachmentDefinition (agents-owned contribution, VLAN 10 `vlan` plugin) + reserved IP | agents Multus / network-infra IPAM | agents#5368, network-infra#774, WX-04 | Planned |
| Collectors ×3, receiver, pipelines ×3, scheduler, intel, api, web | Workloads | `weather` | Slice issues | Planned |
| Weather monitoring | Scrape, probes, rules, dashboards | monitoring-stack | monitoring-stack#1072 + slice launches | Planned |
| Secrets (camera, B2, Garage, Cortex, DB) | KSOPS | agents `secrets-ksops/weather` | owner issues above | Planned |

Adjacent items this repo consumes but does not own: Tempest hub (homeassistant), cameras (sentinel), Cortex routes (cortex-ai-compute), `ak-forwardauth` (network-infra), vallery.net homepage (VerdifyConsultancy/wordpress).

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
| `weather-ci` tooling image | `Dockerfile.ci`, registry-owned repo-build profile | PG16.15/vector0.8.6 disposable in-container integration; never an application database |

These namespace-rendered application objects inherit the central `namespace:weather`
ownership declaration. Fleet validation/profile declarations and the CI image digest
pin are agents-owned; source/tests, schema and runtime image contents remain here.
