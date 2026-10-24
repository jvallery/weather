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

## Tempest ingestion source preparation

Weather owns the `tempest-receiver` role source, its private SQLite WAL/FULL receipt spool, additive telemetry provenance/conflict/coverage schema and receiver metrics. Migration `0002_telemetry` extends schema v1 without changing its snapshot. This source preparation creates no active receiver, PVC, network attachment or credential; #5 commissions those objects after native prerequisites. See [Tempest operations](docs/operations/tempest-ingest.md).
