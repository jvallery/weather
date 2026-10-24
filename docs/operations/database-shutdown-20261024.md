# Weather database shutdown handoff — 2026-10-24

Jason explicitly paused all work for shutdown. Do not submit, retry, cancel,
merge, deploy, create a database or change providers until Jason resumes work.
Existing admitted operations are left untouched. Weather #6 remains OPEN.

## Durable source

The preservation branch is `feat/weather-cnpg-commission-6` in
`jvallery/weather`, based on accepted `e3d4151aa0a507e1dd6e98d328ae962754efe6af`.
It contains only precommission fixes and sanitized proof custody. It has no PR
and is not an activation: database, migration, scheduler and receiver resources
remain excluded from the root kustomization.

CNPG admission rejected a digest-only image because it cannot identify the
PostgreSQL major version. The preserved fix uses `:16.15@sha256:d66df406385b9f3c43c47e94fe3a0002278ef56d21b82a8c9d3a22f91aa9c3f0`
in production, restore and role-probe templates. They also reference the existing
namespace pull Secret `zot-origin-cluster-pull`. Before the pause, all six database
objects passed server-side dry-run; nine focused manifest guards passed. No
objects were persisted by dry-run. No production Cluster exists in the latest
pre-pause observation.

The completed read-only registry check at 16:29:09Z used that existing Secret
inside its consuming process: HEAD returned 200 and the exact d66 digest. The
live Barman sidecar configuration matched accepted agents source and anonymous
GHCR HEAD returned 200 with digest
`9880817c285c7afa4d195da2145064d21907405489ed6ec39abe59b1feb558a4`.
No blobs, new identity or Job were involved. Receipts and verifier sources are
under [database-shutdown-20261024](evidence/database-shutdown-20261024/).
Helper scripts are preserved operator sources with historical absolute paths;
inspect and adapt checkout paths before using them after resumption.

## Delivered prerequisites

- B2 owner issue storage-infra #1112 is closed; storage PR1142 merged
  `e474ffcd2155f7f9e40f97d63ca7817e72e2eb0e`. Dedicated private bucket
  `weather-db-vallery`, writer without permanent delete permission, independent
  restore reader and provider 33-day hide plus 7-day hidden-version expiry were
  verified. Do not add ObjectStore retentionPolicy: provider lifecycle is the
  sole expiry authority. Never recreate or broaden these keys.
- Schema #9 closed at accepted `170aecfc2866ab4b60ab028e3e7eff67df24a861`
  after real in-cluster PG qualification. Telemetry source #57 accepted
  `dd99be06a936b6adda37a355a15e52b0da62c803` adds migration head
  `0002_telemetry`. Monitoring #1072 is closed.
- Disabled CNPG source PR65 merged
  `58fa99581cf212122f65349241f183e1cf5fec9b` after normal three-check CI.
- App build `repo-build-weather-app-dd99be06a936-push`, Workflow UID
  `3f8eee0b-1fcb-4818-a908-7659a4bbc1cd`, succeeded and produced
  `weather-app@sha256:b31d9d54d737d6cc1607618f4aa0ba99f8c6a4c8e256a5b931d825f8984342a1`.
  Promotion PR71 merged `da8b451a388c5271a79e5a320c2c1678be35f800`.
  Normal promotion Workflow `f639ebeb-a685-49ff-a877-b63da4d0f34d`, evidence Pod
  `ed275615-de45-4754-a13a-65c476118742`, passed all three checks. Four independently
  fetched supply-chain JSON objects passed identities, SHA and size checks.
  Two actual API Pods ran b31, UID/GID10001, FFmpeg/ffprobe9.0.2 and libx264;
  exact migration files/head0002 and readiness were verified without opening DB
  connections. Argo was Synced/Healthy at the recorded revision. These are
  historical observations, not a claim about post-shutdown live health.
- App rollback artifact `e4454a5a74c405d923e0a8d8c3c1978cf594b70be46438fc06ee7883ac23c5ca`
  came from accepted source58fa; previous web pin
  `0761f4ebf5f5cf73a4d261446b296fa77c1a3fb7e755585306c218a6e70764bd`
  was preserved by PR71. Scaffold owns newer web PR72; preserve its eventual
  actual pin when rebasing. Root's completed registry owner audit established
  effective whole-repository retention for Weather app/web, including b31,
  candidate web and rollback. No CM/policy change was needed.
- Combined agents PR5481 merged
  `949da543149a68d9e830aa3eee3e33207176a45b` with22/22 accepted native archives
  and real GitHub SUCCESS. Weather secrets became Synced/Healthy at that exact
  revision. All11 original cipher files and ten live Secrets plus the encrypted
  policy matched independent in-memory decryption. Five database basic-auth
  logins exist, but real database authentication is UNRUN. Agents PR5518 is
  CLOSED as superseded; its original head and exhausted/accepted historical
  CI evidence remain preserved. No credentials were regenerated.

## Remaining acceptance after explicit resume

1. Read current main, Argo and source changes first. Preserve all unrelated
   app/web pins. Reconcile this branch genuinely; do not reset shared worktrees.
2. Recheck native dependencies, existing Secrets, exact image authorization and
   current capacity/admission reservations. Storage maintenance #1100 scoped
   node4/5; historical release records are not a current reservation. Require
   three eligible distinct physical hosts for production PG. Do not invent a
   whole-estate maintenance gate when a scoped safe reservation is available.
3. Prepare root kustomization activation of database and migrate only, use
   `Refs #6` in PR descriptions, run normal required CI and protected merge.
   Scheduler/receiver remain owned by later commissioning. Never delete the
   production Cluster, namespace, PVCs, PVs or buckets.
4. Verify3 instances on3 physical hosts, synchronous replica, PG16.15/vector0.8.6,
   generated PVC protections, all5 real role logins and migration head0002 for
   this exact b31 artifact. A future app digest can change that head; verify it.
5. Prove completed base backup, WAL arrival in B2 within5minutes, measured WAL
   capacity/headroom and write continuity through a real planned switchover.
   Keep ObjectStore retentionPolicy absent and no-delete key permissions intact.
6. Run the isolated reader-only PITR drill, prove target sentinel exclusion,
   measured RPO/RTO and no production archiver use. Remove only exact verified
   disposable drill resources through its UID/resourceVersion guard.
7. Integrate monitoring branch `feat/weather-database-alerts-6` at
   `7ddf20bdcd0bc59e36d81117f781e25825ff1790`; it contains two guarded source
   alerts and focused fixtures. Actual target labels, backup/WAL metrics and a
   real WAL-staleness fault/alert proof remain UNRUN. No monitoring PR exists.
8. Publish sanitized live/recovery proof, update runbook/ownership as needed,
   and only then close #6. No user decision is outstanding; explicit resume is
   required because Jason paused work.

## Other preserved source and evidence limits

Remote source refs verified during shutdown:

- agents `archive/weather-db-5518-cea9d3ee1-prepared`:
  `cea9d3ee1b188baf66efe16072964fddc5b66ac4`;
  original PR5518 `feat/weather-db-credentials-6`:
  `8d9bedc45ff0046b34ff959b7b828fedc0837b5b`.
- weather `feat/weather-cnpg-6`: `f22e553f4fc67af4c52b3ec71aa5298f45d29d4f`;
  `deploy/weather-db-artifact-6`: `33501b424c63d38b01504e6d653c430952c00b50`.
- weather API14 contributed sources `feat/weather-evidence-revisions-14`:
  `0b654a90609639438b9fa11c96235e83e7b45371` and
  `feat/weather-aggregate-worker-14`: `bf27df578d2a2e532238dc50452f701d46b7b980`.
  Root already composed these; do not independently deploy them.
- monitoring-stack `feat/weather-database-alerts-6`:
  `7ddf20bdcd0bc59e36d81117f781e25825ff1790`.

Full local orchestration logs, intermediate receipts and diagnostic snapshots
remain at `/Users/jason/maintenance/weather-cnpg-6/` (including build subdirs),
`/Users/jason/maintenance/weather-b2-1112/`, and the shared
`/Users/jason/maintenance/weather-slice1/` evidence reader. Important sanitized
final receipts and verifier sources are copied to this branch; intermediate
raw local logs are not all remotely backed up. CI supply-chain objects have
normal7-day hot custody, not indefinite disaster recovery. The private age key
stays only in its existing approved custody and must never be copied into Git.
No owned producer is intentionally left running by this lane; completed builds
and CI are retained. Root owns unrelated admitted API workflows; do not cancel
or retry them on behalf of this handoff.
