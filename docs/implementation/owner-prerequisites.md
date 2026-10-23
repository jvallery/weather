# Owner prerequisites

These are actual owner issues, not placeholders. Weather's public graph records only references and sanitized acceptance boundaries; owner evidence remains private. Their native blocked-by edges were read back by exact issue ID. They gate implementation and admission, not completion of the planning document itself. WX-00 remains open until research and design qualification are complete.

| Key | Owner issue | Weather consumers | Required outcome |
|---|---|---|---|
| OWN-CI | [jvallery/agents#5366](https://github.com/jvallery/agents/issues/5366) | WX-01, WX-02 | Scoped CI/GitOps/secret delivery and untrusted contribution path |
| OWN-NFS | [jvallery/storage-infra#1106](https://github.com/jvallery/storage-infra/issues/1106) | WX-03 | NFS grant, capacity and fencing qualification |
| OWN-DB | [jvallery/storage-infra#1112](https://github.com/jvallery/storage-infra/issues/1112) | WX-05, WX-11 | Dedicated CNPG block storage and separate bounded telemetry spool admission, capacity, fencing and retained-volume recovery |
| OWN-CAM | [jvallery/sentinel#550](https://github.com/jvallery/sentinel/issues/550) | WX-10 | Three native snapshot sources, bounded cadence/NVR impact, exposure-time calibration and versioned image privacy/region policy |
| OWN-OBJECT | [jvallery/storage-infra#1107](https://github.com/jvallery/storage-infra/issues/1107) | WX-06 | Private object bucket and measured durability boundary |
| OWN-GATEWAY | [jvallery/storage-infra#1108](https://github.com/jvallery/storage-infra/issues/1108) | WX-07 | Enforced media/archive authorization with independent recovery access |
| OWN-CUSTODY | [jvallery/storage-infra#1109](https://github.com/jvallery/storage-infra/issues/1109) | WX-29 | Central full/WAL custody and joint independent restore |
| OWN-UDP | [jvallery/network-infra#774](https://github.com/jvallery/network-infra/issues/774) | WX-04 | Approved direct station packet receiver path |
| OWN-EDGE | [jvallery/network-infra#775](https://github.com/jvallery/network-infra/issues/775) | WX-19 | Qualified same-origin public media transport |
| OWN-MODELS | [jvallery/cortex-ai-compute#402](https://github.com/jvallery/cortex-ai-compute/issues/402) | WX-21, WX-23 | Native temporal video and versioned text embedding qualification |
| OWN-GPU | [jvallery/cortex-ai-compute#403](https://github.com/jvallery/cortex-ai-compute/issues/403) | WX-15 | All-device ownership and bounded GPU or CPU admission |
| OWN-OBS | [jvallery/monitoring-stack#1072](https://github.com/jvallery/monitoring-stack/issues/1072) | WX-31 | Delivered freshness, capacity and recovery signals |
| OWN-EMBED | [VerdifyConsultancy/wordpress#242](https://github.com/VerdifyConsultancy/wordpress/issues/242) | WX-33 | Follows WX-44 qualified card; delivers the actual serving-site integration before combined acceptance |
| OWN-L2 | [jvallery/agents#5368](https://github.com/jvallery/agents/issues/5368) | WX-04 | Pinned VLAN CNI, owner-admitted secondary attachment and fail-closed per-namespace isolation; qualify native pod reception on existing observed trunks |

## Findings that change implementation

The current storage broker is not a drop-in Weather authorization service: its consumer-specific database authority, mandatory metadata and restricted request surface exclude operations needed by media and Barman. WX-07 must specify and qualify a dedicated compatible boundary. Database-archive recovery authorization must work without the failed application database; media-reader policy must not expose originals or archives. The owner issue requires a concrete supported-operation matrix and negative isolation tests.

Central full and frequent WAL custody jobs are active, but Weather is not enrolled. Existing jobs are evidence of a supported integration path, not evidence that Weather data has been backed up or restored.

The native CI event path admits default-branch pushes and same-repository pull requests. It excludes foreign forks. Public contribution execution must therefore remain unprivileged and explicitly qualified; repository visibility does not grant access to trusted fleet execution.

The former main-site repository is archived and rejects new issues. The embed prerequisite is filed in the repository identified by the live serving Application. Historical lane documentation must be reconciled before delivery; implementing against an archived framework is not acceptance.

GPU inventory, object-service health and historic station network reservations are discovery evidence only. They do not establish safe compute admission, power-loss durability or live UDP reachability. Each owner's issue requires current evidence before the dependent workload executes.

## Execution rule

Re-read owner issue status and current admission before claiming a consumer ready. A closed issue with stale capacity, expired credentials or changed serving ownership is insufficient. Run independent ready branches while preserving blocked work; do not weaken the product requirements to bypass an external dependency. The conditional GPU branch does not force GPU use when the required CPU path is qualified.


Owner-side native dependencies are also part of the execution graph: OWN-CUSTODY depends on OWN-OBJECT, and OWN-EMBED depends on WX-44. The [combined readback](embed-order-graph-verification.json) verifies these along with Weather-side dependencies. Parent design coordination occurs during WX-00; completed live parent integration does not block construction of its own prerequisite card.
