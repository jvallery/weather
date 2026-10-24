# Architecture simplification and re-plan — 2026-10-24

**Status:** accepted; amended by the [takeover re-plan](2026-10-24-takeover-replan.md) (C1–C16), which wins where they conflict. Jason chose O1–O4 below on 2026-10-24, after an adversarial audit traced the plan to live estate evidence. The planner simplifications P1–P20 follow from those choices or replace mechanisms that only the planner had introduced. Where this record conflicts with [requirements v4.0](../requirements.md) §§3–18, the ADRs D01–D16 or the contracts, **this record wins**. Product scope Q01–Q16 is unchanged, apart from the R09/R10/R17/R18/R23 amendments listed here.

The authoritative design summary is [architecture.md](../architecture.md). The backlog and native dependency graph are on GitHub.

## Why the plan changed

The live audit (2026-10-23, context `vallery`, read-only) found four things:

- **Nothing could start.** All 46 work issues were transitively blocked by 14 owner issues, every one of which was open, unassigned and uncommented. Most of those owner issues asked owners to build Weather-specific machinery.
- **A peer app already proved the path.** `options-tracker` went from repo creation to Argo delivery in a single day, without any of that machinery. The pattern is one agents change set, a dedicated CNPG cluster on `longhorn-v1-rwo`, Barman going straight to a dedicated B2 bucket, an app-owned IngressRoute behind the wildcard tunnel, and Authentik forward-auth.
- **The hostname is already wired.** `weather.vallery.net` already resolves through the wildcard tunnel and returns the Traefik 404. Multus is installed but has zero NetworkAttachmentDefinitions. Garage per-app buckets with per-bucket keys are the estate norm, with 56 buckets.
- **The largest custom components came from constraints, not needs.** Each was forced by a constraint the planner adopted or an owner text that predated the estate pattern: a SigV4 S3 gateway, NFS writer fencing with host fence receipts, dual UDP receivers with a custom WAL format, joint DB/media checkpoint shards, a promotion working set, motion interpolation with a GPU track, and fork-PR CI isolation.

## Owner decisions (Jason, 2026-10-24)

| ID | Decision | Supersedes |
|---|---|---|
| **O1** | **Split buckets; database backups go directly to B2.** CNPG uses the barman-cloud plugin to write directly to a dedicated B2 bucket. The writer key cannot delete; a separate restore reader key exists (the options-tracker pattern). `weather-media` (public classes) and `weather-originals` (private hourly JPEGs) are separate garage-hdd buckets, each with native read-only and read-write keys from the storage-infra bucket-request pipeline, and central custody mirrors both to B2. No custom S3 gateway. | R17 "one bucket" and the backups part of R23 "in Garage"; §11.1–11.2 single bucket and enforcing gateway; **D08 in full**; D15 joint-checkpoint, closure and canonicalization; `recovery-*` and `hourly-archive-*` contracts |
| **O2** | **Observed-exposure rendering at launch, then an A/B pilot.** The renderer integrates real captured frames, time-weighted in linear light (synthetic shutter). Motion interpolation is not a launch gate. A bounded in-cluster A/B pilot (WX-15, #16) compares observed exposure with `minterpolate` 4× on one camera-hour. Jason decides on interpolation with the clips in hand. GPU co-tenancy is deferred: R10 is satisfied by the D09 census plus a justified CPU baseline. | R09 "high-interpolation" as a launch requirement; §7.3 interpolation profiles; D10 interpolation and flow-adapter parts; WX-15 GPU scope; cortex-ai-compute#403 |
| **O3** | **Incremental public slices.** A walking skeleton ships first, then live weather, then timelapses, then intelligence. Each slice goes public when its launch issue passes. Every slice carries its own backups, metrics, alerts and runbook. WX-35 now means "initial scope complete", not "first deploy". | The WX-31/33/34/35 big-bang acceptance tail |
| **O4** | **A ci-only cell with Mac sessions.** Weather is a `ci-only` cell, like options-tracker. Implementation agents run from Claude/Codex sessions on the Mac and offload builds, tests and pilots to the cluster through the fleet `ci_submission` / repo-build path. No repo pod. | D01 coding-pod and untrusted-fork lanes |

## Planner simplifications

Each item names what it replaces. Mechanisms not listed here keep their current ADR text, but they must be read as implementation guidance, not acceptance gates.

### Delivery and repository

- **P1 Onboarding.** One agents change set, copied from options-tracker:
  - registry record `gh-1431367372` (`desired_state: ci-only`, `ci_submission` profiles for the two images `weather-app` and `weather-web`, `endpoints: weather.vallery.net`)
  - AppProject and Application (app source `jvallery/weather` `deploy/k8s`)
  - `secrets-ksops/weather/`
  - estate-ownership rows

  The repo enters `config/branch-protection-baseline.yml`. Actions are restricted to selected actions with SHA pinning. **Fork pull requests never execute in fleet CI**: Jason re-pushes reviewed changes as same-repo branches. This replaces D01/E11 untrusted-execution qualification.
- **P12 Code-first API.** FastAPI/Pydantic models generate OpenAPI, and `openapi-typescript` generates the frontend types. The hand-written `openapi.json`, `api.schema.json` and fixtures are archived as reference input. `api-contract.md` remains the semantic contract.
- **P19 Tests.**
  - Python: pytest against an ephemeral PostgreSQL+pgvector container, plus a pinned Garage container for S3.
  - Frontend: Vitest + Testing Library, and Playwright (Chromium, WebKit, Firefox) with `@axe-core/playwright` at WCAG 2.2 AA.
  - All heavy suites run in-cluster through fleet CI. Jason does a real iOS Safari check at the slice-2 launch.

### Data plane

- **P2 Staging.** Obsidian NFS staging is kept (R07). It is a dedicated quota'd share and static PV, following the `synology-nfs-grants` precedent. D03 writer epochs, lock inodes, handover proofs and fence receipts are removed. Instead:
  - Each camera's collector is a `replicas: 1` StatefulSet.
  - Chunk names include the pod UID and incarnation, so two writers can never overwrite each other.
  - A chunk is one TAR (frames plus an attempt manifest member) sealed by `fsync` + `rename` into `sealed/<camera>/<utc-hour>/`.
  - The scheduler dedupes accepted frames by `(camera, slot)`; the best attempt wins and duplicates are recorded.
  - Soft-mount errors abandon the chunk and record a gap.
  - The collector keeps R08 isolation: camera-only egress, no database or API access.
- **P3 Streaming pipeline.** Sealed chunks are processed as they arrive, because frame ownership is fixed once the segment boundaries are known. In one pass each chunk yields:
  - finished output frames into a near-lossless per-segment mezzanine on the staging share
  - keogram columns
  - hourly-original candidates
  - the latest preview candidate

  At the segment boundary only concatenation, the three encodes and validation remain. A raw chunk is deletable when all of the following hold: its frames are in a durable mezzanine, its keogram strip is committed, every overlapping hour slot is finalized with its original archived and read back, and its preview window has passed. This replaces the promotion working set, the promotion receipts, the hourly-archive receipt schema and the 48-hour product pins. The database rows are the receipt. Staging occupancy drops from about 1.6 TB to hours of input.
- **P4 Telemetry.**
  - **Receiver.** One receiver Deployment (`Recreate`). It uses a single `vlan`-plugin NetworkAttachmentDefinition over a VLAN-10 trunk worker: static IPAM, reserved /32, no gateway or routes, pinned `vlan` and `static` CNI binaries. The NAD is agents-owned and contributed into `weather`, which keeps the private address out of this public repo.
  - **Pod hardening.** Non-root, no capabilities, read-only root filesystem. It binds only UDP 50222 on that interface and never transmits there. The residual same-L2 risk is documented.
  - **Spool.** SQLite in WAL mode with `synchronous=FULL` on a 1 GiB `longhorn-v1-rwo` PVC. The receiver inserts raw bytes, drains to CNPG in batches, and deletes on commit.
  - **Dedupe.** `(device, message_type, source_ts, row)` with `ON CONFLICT`.
  - **Removed.** The dual receivers, the custom framed WAL, the cross-receiver variant/conflict model and the bespoke CNI namespace guard (D04/D05). A reschedule gap of 1–2 minutes during node loss is accepted and visible in coverage.
- **P5 Database.** Copy `options-pg`:
  - 3 instances on `longhorn-v1-rwo`, `minSyncReplicas`/`maxSyncReplicas` 1, required `topology.vallery.net/proxmox-host` anti-affinity
  - the PostgreSQL 16.15 mirror image with pgvector (as research-vault uses)

  Further choices:
  - **No partitioning in v1.** About 11M telemetry rows a year is comfortably unpartitioned; use BRIN on time. Revisit above about 200M rows.
  - **Migrations.** Alembic, run by an Argo Sync hook Job (wave 1) under a dedicated migrator role; DB clients run at wave ≥2 (amended by the [takeover re-plan](2026-10-24-takeover-replan.md)).
  - **Capture metadata.** CNPG stores per-chunk and per-slot rows; per-attempt detail stays in the chunk manifest.
  - **Durability.** `dataDurability: required` is not selected. Standard sync 1/1 with `synchronous_commit=on`. This replaces the D06 synchronous/failover-quorum/R1-admission work and storage-infra#1112's storage admission.
- **P6 Objects.**
  - **`weather-media`** holds videos, posters, keograms, sanitized stills and sidecars. The API's media origin holds only its read-only key.
  - **`weather-originals`** holds the private hourly JPEGs. Its read-write key exists only in the image worker; no web identity can read it.
  - **Keys.** Content-addressed and immutable (`<class>/<sha256>.<ext>`). Writers never overwrite a different hash.
  - **Previews** are not stored in Garage or custody: the pipeline writes them to the staging share's `previews/` directory, which `api` mounts read-only, with a DB pointer. R18 holds natively, because no media identity can read backups or originals.
- **P7 Recovery.**
  - **Database.** Barman sends WAL and daily base backups to B2 with `archive_timeout` 300 s, then runs a PITR restore drill. DB offsite age is under 30 minutes by construction. The declared PITR window is **30 days**, enforced by B2 lifecycle rules because the writer key cannot delete.
  - **Media.** Central custody `mirror` rows cover both buckets.
  - **GC grace.** 90 days, which exceeds max(PITR window, B2 version retention) + 7 days. No object referenced by any restorable catalog state is ever deleted.
  - **After a restore,** a reconcile job HEADs every referenced object and marks missing ones `unavailable`; the UI already models that state.
  - **Joint offsite age** is a metric: `max(db_offsite_age, age of the oldest object not yet custody-verified)`.
  - This replaces the catalog-freeze advisory lock, closure shards, JCS vectors and verifier ledger.
  - **Third copy.** The DR-01 third-copy question for `weather-originals` is presented to Jason as a costed option in WX-35. It is not a slice gate.

### Products and compute

- **P8 Media origin.** Authorization happens at request time for each GET, HEAD and range against the committed publication row, including the `public_not_before` embargo. There are no mid-stream authorization leases. The D14 initial cache policy holds. Cloudflare caching (WX-27) is closed as not planned and recorded as a future option in D14.
- **P9 AI descriptions.**
  - **Input.** Sampled frames from each published rendition (per hour window) plus the segment keogram, sent through the proven image path on `llm.primary.longctx.mm`. Native video input is an optional upgrade if Cortex later qualifies it.
  - **Prompts.** The §13 templates and schemas remain. A simple validator rejects any number not present in the evidence packet.
  - **Execution.** Concurrency 1 via a Postgres advisory lock, a client timeout, a bounded nightly retry, and a raw-response table with `expires_at`.
  - **Model changes.** Analysis is pinned to the model and prompt version at publication. There is no automatic archive reanalysis.
  - This replaces the D11 call-ownership state machine, the diagnostics ceilings and the native-video gate.
- **P10 Search.**
  - **Embeddings.** Video-level documents (about 11k over 5 years) use `embed.main` (2,560-dim) with exact cosine; no ANN.
  - **Hybrid.** Window-level matches come from PostgreSQL FTS and the typed measured filters, fused with semantic results by RRF.
  - **Profile changes.** A `profile_id` column; on change, re-embed and flip config. No shadow generations or query-vector cache.
  - **Limits.** Rate and concurrency limits use a Traefik `rateLimit`/`inFlightReq` middleware plus one in-process semaphore.
  - **Inference outage.** Numeric-only search works with inference offline.
- **P11 Editor auth.** Authentik forward-auth (`network-infra/ak-forwardauth` + `apps-strip-auth-headers`) protects `/editor` and `/api/v1/operator/*`. The API trusts the identity header only from Traefik (NetworkPolicy) and checks a one-user allowlist. CSRF protection is `SameSite=Strict` plus an Origin check. This replaces the in-app Authlib OIDC design (`operator-identity-design.md`).
- **P13 Time model.**
  - Keep Astral (civil twilight, depression 6°), half-open segments, the exact 7,200-frame allocation and the golden `time-model-vectors.json`.
  - Hour slots derive from the UTC hour; America/Denver offsets are whole hours.
  - A `weather_day(local_date)` table carries the Q03 calendar-day FK. 23/24/25-slot days fall out of `generate_series`.
- **P14 Previews.** A frame is eligible when `capture_upper_bound + 600 s + skew_margin ≤ now`. The skew margin defaults to 30 s on NTP-synced nodes, and a clock-offset alert covers it. Unknown or implausible timing is never eligible. Responses are served `no-store`.
- **P18 GPU.** WX-15 (#16) becomes the interpolation A/B pilot. OWN-GPU (cortex-ai-compute#403) is closed and will be re-filed only if the pilot and Jason select a GPU-requiring profile.

### Operations, acceptance and documentation

- **P15 Monitoring.**
  - Delivery follows the options-tracker pattern: Weather exposes `/metrics`, and monitoring-stack scrapes it through its static `kubernetes_sd` jobs (PodMonitor CRs are not read in this cluster). Rules, probes and dashboards are PRs to monitoring-stack. Weather NetworkPolicies admit `observability` to the metrics ports.
  - **SLOs** (30-day windows):
    1. Segment published within 2 h of its boundary for ≥95% of segments.
    2. Current-conditions age ≤90 s for 99% of the time while the station is reporting.
    3. Public page and media probe availability ≥99.5%.
    4. DB offsite age ≤30 min.
  - Everything else is a signal, not an SLO.
  - **Service tier: Tier 2.** NAS-dependent capture and media outages are accepted. STD-HA-01 does not apply.
- **P16 Acceptance.** Every issue meets the common definition of done in [AGENTS.md](../../AGENTS.md). The integrated WX-31/33/34 issues are dissolved into per-issue DoD and the slice launch issues WX-47/48/49. Inherited estate gaps (STD-DR-05 bootstrap, CI-04 signing admission, the CNPG primary PDB) are disclosed, not gated.
- **P17 Documentation.** Planning audits, clause audits, validation receipts, graph verifications and superseded contracts move to [docs/archive/planning-2026-10/](../archive/planning-2026-10). They are history, not instructions. The authoritative set is in the [README](../../README.md).
- **P20 Defaults for the decisions the audit found missing.** Each default is cheap to change.

| Topic | Default |
|---|---|
| Display units | US customary (°F, mph, inHg, in), with a metric toggle |
| Language | English only |
| History at launch | Starts empty. Importing Tempest-cloud or HA history would contradict "direct UDP is the ingestion path" and needs Jason's explicit request. |
| Station replacement | A new `device` row with validity dates. Records and almanac continue across devices at the same site. |
| Video retention | 365 days |
| Hourly originals | 5 years. No deletion job is enabled before WX-35. |
| Hard purge (third-party privacy) | A runbook that overrides GC grace on Jason's explicit instruction and leaves an audit row. |
| Exports | JSON as specified. CSV is an open question for Jason in WX-35. |
| Clocks | Node chrony is proxmox-infrastructure's. Camera NTP is part of sentinel#550. The clock-offset alert is in the monitoring PR. |

## Owner prerequisites after the re-plan

The Weather execution agent authors each change set in the owning repository; the owner issue records it. Each owner issue is a one-PR change, not a qualification program.

| Issue | Re-scoped ask | Blocks |
|---|---|---|
| [agents#5366](https://github.com/jvallery/agents/issues/5366) | Weather onboarding change set (P1) | WX-01 |
| [agents#5368](https://github.com/jvallery/agents/issues/5368) | Pinned `vlan` + `static` CNI plugins and one agents-owned VLAN-10 NAD contributed into `weather` (P4) | WX-04 |
| [network-infra#774](https://github.com/jvallery/network-infra/issues/774) | Reserve the receiver /32 on VLAN 10; record the UDP 50222 consumer | agents#5368 |
| [network-infra#775](https://github.com/jvallery/network-infra/issues/775) | Record `weather.vallery.net` as a public read-only route via the wildcard tunnel (no tunnel change) | WX-47 |
| [storage-infra#1106](https://github.com/jvallery/storage-infra/issues/1106) | Weather staging share + static NFS PV with quota (P2) | WX-03 |
| [storage-infra#1107](https://github.com/jvallery/storage-infra/issues/1107) | `weather-media` and `weather-originals` bucket-request rows, keys into KSOPS, custody `mirror` rows, B2 budget (P6/P7) | WX-48 |
| [storage-infra#1112](https://github.com/jvallery/storage-infra/issues/1112) | Dedicated B2 bucket for CNPG archives + scoped writer/reader keys into KSOPS (O1) | WX-05 |
| [sentinel#550](https://github.com/jvallery/sentinel/issues/550) | Consent to ≤1 Hz snapshot polling; a view-only camera user per camera; camera NTP check | WX-03 |
| [cortex-ai-compute#402](https://github.com/jvallery/cortex-ai-compute/issues/402) | Scoped GPUStack consumer key for Weather (`llm.primary.longctx.mm` image path, `embed.main`) | WX-49 |
| [monitoring-stack#1072](https://github.com/jvallery/monitoring-stack/issues/1072) | Weather scrape, probe and slice-1 rules (freshness, backup age, clock offset); extended per slice | WX-47 |
| [VerdifyConsultancy/wordpress#242](https://github.com/VerdifyConsultancy/wordpress/issues/242) | `frame-src https://weather.vallery.net` in `wp-content/themes/vallery/functions.php` + homepage block after "What I am doing now" | WX-35 |

Closed: storage-infra#1108 (gateway; not planned under O1), storage-infra#1109 (merged into #1107), cortex-ai-compute#403 (GPU deferred under O2).
