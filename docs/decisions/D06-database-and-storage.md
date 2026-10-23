# D06 — dedicated PostgreSQL, native partitions and explicit durability

Status: **database architecture selected; storage/capacity and failover qualification pending**. Requirements R14/R16/R20/R22/R23/R28/R30; WX-05/08/11/28/32. Storage admission is [OWN-DB](https://github.com/jvallery/storage-infra/issues/1112); the existing snapshot NFS prerequisite does not cover database volumes or telemetry spools.

## Decision and alternatives

Use a dedicated three-instance CNPG cluster in Weather, native PostgreSQL partitions and pgvector. Reuse the installed operator/Barman integration and a qualified locally mirrored image, not another operator or an independently maintained database appliance. The current PostgreSQL 16.15/pgvector 0.8.6 image is a verified packaging candidate; pin its exact digest and revalidate supported versions, migrations and restore before delivery.

A shared database is not selected: physical backup and restore encompass its whole cluster and would put other tenants' data and recovery coupling into Weather's bucket and failure domain. A dedicated cluster costs three instance/volume reservations but provides independent credentials, schema, migration, backup and recovery ownership. Two instances reduce failure/recovery flexibility; three is the selected topology subject to real capacity admission, not an assumption that capacity exists.

Native partitions avoid an additional Timescale packaging, licensing, migration and recovery dependency for one station. Detailed receipt/history retention is unlimited by policy until explicitly changed, with measured capacity expansion; rollups do not authorize deletion. Revisit compression or Timescale only against measured need and an exact component/license proposal. Full-precision embeddings and exact filtered search remain the baseline; no unsupported 2,560-dimensional vector ANN index is implied.

## E05: current evidence

Two existing primaries were queried read-only in the `postgres` database. Both report PostgreSQL 16.15, `synchronous_commit=on`, a one-standby quorum with both standbys streaming, and available pgvector version 0.8.6. No Timescale extension entry was returned. `installed_version=null` describes only the queried database; it does not establish the extension state of another application database. Image packaging does not prove successful Weather extension creation, schema migration or recovery.

Current fleet clusters use physical-host anti-affinity, but storage/replication choices vary. The restricted CNPG R1 class has strict-local placement, one storage replica, delayed binding and Retain reclamation. Its pinned consumer inventory lists three existing consumers and excludes Weather. Neither a visible StorageClass nor a healthy tenant is a Weather grant. E05 is retained privately in the storage owner prerequisite; it includes exact image IDs, source revision, placement metadata and query results, without credentials or application data.

The installed Cluster CRD supports explicit synchronous method/number/durability and failover-quorum fields. Shared controller and Barman deployments each have two Ready replicas, but their custom image digests require owner-supported version/provenance verification. No operator change is proposed here. E05 receipt SHA-256: `7823375d0d71c326a0d69124923b903c1e2725e15a4fc263a7f93d930ccf8b4f`; the exact source revision and observation time are retained in the private receipt.

## Write and failover policy

Select explicit `method: any`, `number: 1`, `dataDurability: required` with `synchronous_commit=on`, rather than inferring permanent durability from legacy min/max settings. Require physical-host separation for primary and standby instances. Qualify the installed operator's failover-quorum mechanism and partition/fencing behavior before enabling automatic failover for Weather; no acknowledged-write-loss promise rests on a CRD field alone.

With this policy, transactions wait for local and required standby WAL flush; loss of all usable synchronous standbys blocks new commits. Bounded telemetry spools absorb the admitted outage, and APIs may serve honestly aging cached data. Do not silently switch to asynchronous writes to make health green. A returned timeout can have an uncertain commit outcome; retry immutable receipt/publication identities rather than inventing a new write. Storage hardware flush honesty and actual failover candidate selection still matter.

[CNPG replication](https://cloudnative-pg.io/docs/1.29/replication/) documents the required/preferred availability tradeoff; [CNPG failover](https://cloudnative-pg.io/docs/1.29/failover/) describes failover and quorum constraints. [PostgreSQL WAL settings](https://www.postgresql.org/docs/16/runtime-config-wal.html) distinguish flush from apply acknowledgement. Reviewed 2026-10-23. These sources explain behavior, not Weather qualification. Read-after-write paths use the primary unless an explicit replica replay barrier is satisfied.

One physical host loss should leave a viable database pair under the admitted topology; qualify that claim, client reconnect and old-primary fencing. Multiple host/storage failures may block service or require recovery. Local synchronous replication is not offsite backup, and three DB instances do not preserve NAS-backed media availability.

## Storage and admission

OWN-DB compares supported block classes, replication layers and retained-volume recovery. Prefer the existing restricted R1 pattern only if its owner admits Weather with matching backup, failure-domain and N-1 recovery obligations. Do not multiply database and storage replication blindly, bypass the policy, or silently choose a more replicated class without accounting for cost. A different owner-approved class must meet the same documented failure requirements.

Database and independent receiver spools stay off snapshot NFS. Size data, indexes, global receipt/capture registries, vectors, temporary operations and WAL separately from physical backup capacity. Bound WAL generation/outage/replay demand; archive failure can fill local storage even while queries work. Monitoring must show remaining WAL runway and archive backlog before writes exhaust disk. Initial GiB, requests/limits, connection budget, quotas and outage duration remain measured admission values, not copied from an existing tenant.

Maintain one fenced writer per retained telemetry spool, distinct receiver identities and explicit uncommitted-data reconciliation. Existing CNPG storage admission does not automatically cover those standalone volumes; OWN-DB must name their supported class/placement and failure behavior separately.

## Partition and identity policy

Partition receipt payloads by immutable receive month and normalized observations by source month. Keep stable receipt and capture registries outside time partitions to enforce replay uniqueness and durable foreign keys. Precreate upcoming partitions; retain late historical partitions and bound maintenance concurrency. Malformed/impossible source times stay in raw/quarantine records rather than causing arbitrary future partition creation.

Use a bounded default/quarantine path when a partition is unavailable, with visible backlog and transactional relocation after the correct partition exists. A partition maintenance failure cannot silently drop a receipt or mark normalization complete. Qualify default-partition locking and movement under concurrent ingest; never attach overlapping ranges or detach retained history from the query API as an unannounced retention action. Native indexes, partition maintenance and indefinitely growing identity registries need measured space/query budgets.

Migrations enforce day/hour/camera/source relationships and asset-class constraints under the [migration contract](../implementation/database-migration-contract.md). Retained originals cannot cascade-delete with shorter-lived video records. Use scoped roles, verified TLS, bounded SQL and transactional revision/outbox interfaces; public readers do not access private raw tables. Barman archive authorization remains independent of the Weather application database.

## Acceptance and rollback

WX-05 validates exact image/extension versions, role/TLS isolation, admitted volume placement, effective synchronous settings and physical-host separation. WX-08 tests late/future/duplicate receipts, partition creation/move failures, registry uniqueness, migrations and enforced relationships. WX-11 tests spool outage/replay and uncertain acknowledgement. WX-28/32 qualify WAL exhaustion prevention, primary/standby/quorum/partition failure, retained-volume recovery and B2-only restore with extensions/vectors intact.

Tests that disrupt storage or failover use the admitted isolated path; no existing tenant is a destructive test target. Preserve old images, schema compatibility, retained PVCs and base/WAL archives through upgrades. Rollback never means dropping new history, deleting retained PVCs or reusing an old archive identity for a divergent restored cluster. Until these gates pass, this is a selected design with explicit limits, not certified HA or zero-loss operation.
