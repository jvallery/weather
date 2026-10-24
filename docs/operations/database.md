# Weather database operations

## Commissioning state

The source under `deploy/k8s/database/` is excluded from the application until
weather#2, weather#9, monitoring-stack#1072 and storage-infra#1112 close and the
five login Secrets exist in agents KSOPS. This runbook describes the intended
operation; source validation is not deployment, backup or restore proof. Live
evidence, measured WAL headroom, switchover downtime and PITR RTO/RPO are added
when weather#6 commissions the database.

## Topology and identities

`weather-pg` has three instances on `longhorn-v1-rwo`, with 20Gi per instance,
required anti-affinity across `topology.vallery.net/proxmox-host`, synchronous
replication min/max 1 and `synchronous_commit=on`. Control-plane nodes are
excluded. Per database instance requests are 1 CPU/2Gi, limits 2 CPU/4Gi; the
Barman sidecar adds requests 100m/128Mi and limits 1 CPU/512Mi. A restore drill
adds one similarly bounded instance and one 20Gi PVC. Placement must be checked
against current allocatable resources, reservations, pressure and namespace
quota again before commissioning or a drill.

The Zot image is pinned by digest and was verified against the running Research
Vault primary: PostgreSQL 16.15 and pgvector 0.8.6. Initdb creates database
`weather`, owner `weather_migrator`, and the `vector` extension. Managed roles
`weather_ingest`, `weather_worker`, `weather_api`, `weather_editor` have login but
no superuser, createdb or createrole; the API role has a 40-connection limit.
Migrations supply their grants. The five basic-auth Secrets are
`weather-pg-<role>` in agents `secrets-ksops/weather`, with `username` and
`password`. No database credential belongs in source, a connection URI printed
to a log, or an issue. Superuser password access is disabled.

Private packet identifiers must also stay out of PostgreSQL diagnostics.
`log_error_verbosity=terse` removes duplicate-key DETAIL, both parameter-length
settings are zero, and SQL statement/duration logging is disabled, including
error statements below PANIC. Generic CSV error/SQLSTATE diagnostics remain.
The application must still parameterize writes and validate input: terse mode
is not a sanitizer for arbitrary values in an error's primary message. Do not
enable verbose statement/parameter logging on production to debug ingest.

The migrator runs as a `Sync` hook at wave 1 after the Cluster at wave -5, with
bounded role-login readiness. It uses the projected `weather-pg-migrator`
credentials plus `weather-pg-ca` and verifies TLS for `weather-pg-rw:5432`.
It must never be a PreSync hook during first commissioning. Database-client
Pods carry `weather.vallery.net/database-role` with one of the five role names.
NetworkPolicies allow those clients on 5432, `cnpg-system` on the instance API,
observability on 9187 and same-cluster replication. Instance HTTPS egress covers
the Kubernetes API and B2; native NetworkPolicy cannot select a B2 FQDN. The
bucket key and generated CNPG Kubernetes RBAC restrict authority.

`deploy/acceptance/restore/role-logins.yaml` is an excluded, bounded acceptance
Job template. Render a unique operation and future UTC expiry after all five
Secrets and the CA exist. It mounts only those basic-auth Secrets plus the CA,
has no ServiceAccount token, and logs only role names and privilege booleans.
Every login verifies TLS and proves actual password authentication without
superuser/createdb/createrole; a migration's ability to `SET ROLE` is not that
proof. Retain terminal Job output and remove the exact disposable Job after
verification. Never print a password or place it in a command argument/URI.

The Cluster and ObjectStore carry `Prune=false,Delete=false`; the namespace and
production PVC protection must remain in place. Never delete a production
Cluster/PVC/namespace to redeploy. CNPG's primary PDB is an inherited disclosed
estate limitation, not a reason to change a shared operator or force a drain.

## Backup freshness

The Barman Cloud plugin writes directly to
`s3://weather-db-vallery/cnpg/weather-pg` using the bucket-only
`weather-pg-backup-b2-writer` Secret. Daily plugin base backups run at 09:15 UTC,
prefer a standby and run immediately at commissioning. Data and WAL use gzip.
`ObjectStore weather-pg-b2` has no `retentionPolicy`: the writer cannot
permanently delete versions. Storage-infra owns provider retention and the
independent restore reader; custody/capability evidence is
`jvallery/storage-infra:inventory/weather-b2-custody.json`.

Monitoring-stack#1072 scrapes `estate-cnpg` with `namespace=weather` and
`cnpg_cluster=weather-pg`. It alerts when
`barman_cloud_cloudnative_pg_io_last_available_backup_timestamp` is missing or
older than 93,600 seconds (26 hours), sustained for five minutes. Inspect
`kubectl --context vallery -n weather get backups,scheduledbackups,objectstores`
and the Barman sidecar's generic failure category. Never dump Secret data or a
credential-bearing command while diagnosing provider access. Recover the exact
KSOPS identity from custody instead of minting a replacement.

The policy `recovery.pitr_window_days: 30` is recorded in
`ConfigMap/weather-database-policy`. Provider lifecycle hides `cnpg/` objects at
33 days and deletes hidden versions seven days later. Daily bases plus that
margin retain a base preceding the intended oldest target; 40 days remains
below the 90-day media GC grace minus seven days. A new archive's actual history
starts at its first successful base; it cannot prove 30 days of history on day
one. Check the oldest usable base/WAL chain before selecting a historical
target. Long base-backup gaps can shorten the available PITR window.

## WAL archive freshness

`archive_timeout=300s` bounds idle-station WAL segment closure. For every forced
`pg_switch_wal()` acceptance probe, retain the exact segment identity and verify
its B2 presence within five minutes; `ContinuousArchiving=True` alone is too
weak. `cnpg_pg_stat_archiver_seconds_since_last_archival > 1800`, sustained for
five minutes, fires `WeatherDbWalArchiveStale`. The recording rule
`weather:slo4_db_offsite_ok:ratio` measures the 30-minute offsite-age objective.
Missing observations count as failure in the monitoring contract.

On an archive outage inspect Barman connectivity, key binding and provider
metadata, and retain the instance/WAL state. Never delete WAL by hand, disable
archiving as a recovery shortcut, or allow a deleting application writer. The
instances/PVC rule extension in monitoring-stack alerts on fewer than three
ready production instances and a production database PVC above 80%.

The WAL headroom calculation is measured at commissioning, then repeated after
Tempest ingest starts. Sample `pg_stat_wal.wal_bytes` across a known interval;
also measure filesystem free bytes on the primary. PostgreSQL's 16MiB segments
and a five-minute switch give a floor of `288 * 16MiB = 4.5GiB` per 24 hours.
Use `max(measured_bytes_per_second, 16MiB / 300) * 86400`, add normal retained
WAL/checkpoint allowance and current database growth, and compare to actual
free capacity. `max_wal_size=4GB` is a checkpoint target, not a limit during a
failed archive; `wal_keep_size=1GB` also consumes space. Do not claim a 24-hour
outage envelope until the measured calculation fits. Expand storage through
source when needed; never recreate or truncate a volume.

## Switchover

Read `kubectl --context vallery cnpg status weather-pg -n weather`, select an
actual healthy streaming standby and keep sentinel writes running. Promote it
with `kubectl --context vallery cnpg promote weather-pg <standby> -n weather`.
Record the last successful write, first resumed write, new primary, one sync
standby, the three distinct host labels and sentinel continuity. A failed write
is outage evidence and must not be dropped from the downtime interval.

## Reader-only PITR drill

`deploy/acceptance/restore/drill.yaml` is excluded from Argo and is rendered by
`scripts/restore-drill.sh`. The resources use the required `weather-drill-*`
prefix, disposable/expiry labels, and namespace `weather`; the reader Secret
is referenced in place. The drill ObjectStore uses only
`weather-pg-backup-b2-reader`. The recovery Cluster has no `spec.plugins` WAL
archiver, no managed-role/password rewrite, and no ScheduledBackup. Its
NetworkPolicy admits the operator only, DNS and HTTPS; application ingress and
connections to production 5432 are not admitted. Production archives continue
normally; read-only identity authority plus the absent drill archiver prevent
drill writes under production `serverName`.

Plan without making a cluster change:

```sh
scripts/restore-drill.sh --operation pitr-20261024-r1 \
  --target 2026-10-24T03:00:00+00:00 \
  --evidence /tmp/weather-pitr-20261024-r1.json
```

After the native dependencies close and safe capacity/current prerequisites
are verified, run the same operation with `--run`. The script inserts three
retained `weather_recovery_drill.sentinel` rows in production, one per minute,
selects a target between the second and third commits, forces and waits for WAL
archival, and recovers using the independent reader. It requires exactly
sentinels 0/1 present and 2 absent, plus vector 0.8.6. The receipt's measured RPO
is target minus the latest restored sentinel, with a 60-second sentinel
resolution; records intentionally after the target are not claimed as loss.
RTO covers recovery apply through verified readiness/readback. These are test
results, not a claim about every historical production record.

A wait timeout preserves the same Cluster and evidence. Inspect its exact
handle; `--verify` waits/reads the existing operation and never restarts it.
Do not rerun `--run` with the same ID, create a replacement because observation
expired, or mark an incomplete restore as successful. Retain and commit the
metadata-only JSON receipt before exact disposable cleanup using `--cleanup`.
Cleanup requires verified evidence, the matching operation labels and PVC owner
UIDs; it removes the drill Cluster, its PVCs, reader ObjectStore and isolation
policy, and records absence. No production data, Secret, bucket or backup is
deleted. A failed drill is preserved for investigation.

After a production recovery, commission a new Barman `serverName` for that
incarnation. Never send a recovered writer into the source archive prefix.
The post-restore media reconcile/mark-unavailable step belongs to the later
joint recovery issue weather#33.
