# Weather ownership

What this repository owns, where each item lives, and its current state. Central record: agents `control-plane/agent-fleet-control/registry/policy/estate-ownership/`. Update both in the same change set.

State as of 2026-10-24: namespace guardrails are deployed at `bed09184608c947dc0b95a784d8ca7f2facde28f`; Argo is Synced + Healthy. Application commissioning remains pending. Evidence: [baseline receipt](docs/operations/evidence/foundation-baseline-20261024.json).

| Item | Kind | Where | Delivered by | State |
|---|---|---|---|---|
| `jvallery/weather` | Repository (public, MIT) | GitHub | — | Fleet registry `gh-1431367372` enrolled; source and live onboarding tracked in agents#5366 |
| `weather` | Namespace (PSA restricted, quota, default-deny) | k3s `vallery` | WX-01 | Deployed; Active, PSA restricted |
| Quota `weather-budget`, LimitRange `weather-defaults`, default-deny and role policies, tokenless role ServiceAccounts | Namespace guardrails, no RBAC | `weather`, rendered by `deploy/k8s` | WX-01 | Deployed; 12 tokenless accounts and four policies; no application or ingress enabled |
| `weather` | Argo Application (source `deploy/k8s`) | agents catalog / AppProject `weather` | agents#5366, WX-01 | Synced + Healthy at namespace baseline revision; application disabled |
| `weather.vallery.net` | Hostname (backend + route owner) | IngressRoutes in `weather` via wildcard tunnel | WX-01, network-infra#775 | DNS wildcard resolves; returns Traefik 404 today |
| `weather-app`, `weather-web` | Images, digest-pinned | `registry.vallery.net/jvallery/` | WX-02, WX-01 | Reproducible recipes in WX-02; in-cluster build/runtime qualification remains WX-01 |
| `weather-python-base`, `weather-node-base` | Upstream runtime mirrors, imported in-cluster and digest-pinned | `registry.vallery.net/jvallery/` | WX-02, agents#5366 | Imported via fixed fleet profiles; both manifests/configs SHA-verified as Linux amd64 at the pinned runtime versions (see docs/operations/base-imports.json) |
| `weather-pg` | CNPG Cluster, 3 instances, `longhorn-v1-rwo`, 20Gi each | `weather` | WX-05 | Source prepared under `deploy/k8s/database/`, excluded until prerequisites close; not deployed |
| CNPG archive | `weather-pg-b2` ObjectStore + `weather-pg-daily` ScheduledBackup; direct B2 | `weather`, B2 bucket owned by storage-infra | storage-infra#1112, WX-05 | Provider/key capability custody verified; application backup/PITR not commissioned |
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
