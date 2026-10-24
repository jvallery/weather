# Takeover audit and re-plan — 2026-10-24

**Status:** accepted. Jason asked Claude to take over Weather from Codex. He asked for a full audit of the delivered work and every open issue, an adversarial review of the code, and a re-plan. This record holds the outcome. It **amends** the [simplification record](2026-10-24-simplification.md), and where they conflict, this record wins. The design summary stays in [architecture.md](../architecture.md). GitHub issues and their native dependencies remain the work plan.

## How the audit was done

The audit was read-only, run on 2026-10-24 between 16:40Z and 17:15Z against `origin/main` `e3d4151` and the live `vallery` context. Twelve independent auditors covered:

- telemetry, capture, core and web code
- delivery and CI
- the unmerged PRs and branches
- requirements traceability
- slice 0/1 and slice 2/3 issues
- cross-repo owner prerequisites
- live state
- process weight

Each medium-or-higher finding was then checked by an adversarial skeptic. Of 188 findings, 185 survived and 3 were refuted. Local `scripts/ci.sh lint` and the Python typecheck and unit tests passed: mypy was clean and 157 unit tests passed. The web checks run in fleet CI.

## What is actually delivered

- **Live:** Argo `weather` is Synced+Healthy at `e3d4151`. The API runs 2/2 on the foundation image, which serves only `/healthz`. The web runs 2/2 on the *scaffold* image, a "coming soon" shell without CSP; the WX-20 SSR app is merged but not deployed. No `weather-pg`, receiver, collector or NAD exists. No slice-1 feature is live.
- **Merged code, fixture-tested only:**
  - the time model (correct, no defects found)
  - the Tempest ingest library
  - the collector library
  - the schema v1/v2 and job queue
  - the web SSR app (about 950 lines, sound security posture)
  - the CNPG, backup and PITR-drill manifests (not deployable as written)
  - the pinned supply chain
- **Unmerged:**
  - PR #59, the API: 16,419 lines in 86 files
  - PR #68, the almanac: conflicting
  - PR #72, the web digest pin plus evidence JSON
  - `feat/weather-cnpg-commission-6`, which has a real 4-line CNPG fix buried in about 1,300 lines of receipts
- **Owner prerequisites delivered:**
  - agents#5366 (onboarding)
  - network-infra#774 (the receiver /32) and #775 (the route record)
  - storage-infra#1106 (the staging PV) and #1112 (the database B2 bucket and keys)
  - monitoring-stack#1072 (rules and probe)
  - the five database login Secrets (agents PR #5481, commit `949da54`; the handoff's "agents949" means this commit)

## What went wrong

The simplification record removed the over-engineering from the *design*. It came back in how the work was *run*.

1. **Self-imposed launch gates stalled slice 1.**
   - **#21 web.** It was natively blocked on network-infra#799, a Cloudflare RUM exclusion. The only thing that failed was Codex's own held-tab rollout harness (`web/e2e/rollout.mjs`), which throws on any cross-origin script in the DOM. The web image's nonce CSP (`web/server.mjs:23`) already blocks that beacon. Holding the promotion kept the *un*-CSP'd scaffold live, which is worse for privacy.
   - **#48 probe window.** The "24 h green" probe was read as zero failures, and a single DNS timeout reset the clock. That is stricter than SLO 3 (≥99.5%), and it was measured on the placeholder page.
   - **#6 database.** It had zero open blockers but was never commissioned. Instead it waited on "safe placement" evidence, while live capacity showed room on four hosts.
   - **#5 receiver.** It requires a hub source IP that P4 had explicitly dropped.
2. **Complexity regrew in code.**
   - #12 shipped the cross-receipt conflict, integrity, loss-snapshot and provenance model that P4 removed: about 10 KB of JSON per 190 B packet.
   - Migration 0001 freezes 24 tables that no code uses.
   - PR #59 adds an append-only evidence system nothing reads: per-row telemetry copies, an outbox event per observation, a retained aggregate-revision store and a worker. Payloads carry up to 50k evidence IDs, and the committed freshness policies are empty, so no metric can ever be fresh.
3. **Real defects** that would bite on live data. Each now has an issue:
   - **Drain.** An unindexed per-receipt scan stalls the drain under its own 30 s timeout. One failing receipt blocks the whole batch forever.
   - **Data integrity.** Device rows are never seeded. Rain is impossible by design. One bad spool row discards the whole spool.
   - **Queue.** One unrouted outbox topic blocks all dispatch, and `claim()` has no kind filter.
   - **Collector.** Slot keys drift off the cadence grid, breaking P2 dedupe.
   - **Edge.** The public rate limit keys on the three cloudflared pods, so it is effectively global.
   - **Database.** The CNPG manifest on main fails admission because the image has no `:16.15` tag and there are no `imagePullSecrets`.
4. **Process weight.**
   - Dated receipts committed to the repository: 15 evidence JSON files on main plus a 14-file handoff.
   - Comment volume: 124 comments totalling 189 KB, dominated by "exact", "UID", "custody" and "readback".
   - Repository sprawl: 44 remote branches, 85 local `*weather*` checkouts.
   - CI churn: 126 CI workflows in 12 hours, 47 of them failed.
   - Hand-named one-off qualification workflows.
5. **A real decision was hidden.** The camera snapshot 403 (sentinel#550) blocks all of slice 2. The handoff reported it as "no Jason decision is requested now".

These remain sound and are **kept**:

- the CSP and nonce, the host allowlist and bounded upstream reads
- the database-enforced public embargo (`public_not_before ≥ captured_upper + 630 s`)
- byte-identical 404 denials
- per-bucket keys, and a B2 writer key that cannot delete
- the reader-only PITR drill
- `Prune=false`/`Delete=false`
- the rule that the receiver never transmits on the NAD
- privacy greps, gitleaks, digest pins and non-root images
- predecessor web-asset retention

## Decisions

| ID | Decision | Replaces |
|---|---|---|
| **C1** | **No self-imposed gates.** network-infra#799 is non-blocking hygiene, because the CSP neutralises the beacon. Old-tab safety means kept predecessor assets, one check that an old asset still returns 200 after a rollout, and a reload-on-chunk-error handler. No held-tab rollout qualification. A 24 h probe check means availability ≥99.5% over the trailing 24 h, measured after the relevant digests are live; failures count against the ratio and don't reset it. Placement is proven by scheduler admission plus `kubectl cnpg status`; there are no placement-reservation programs. | Handoff gates on #21, #48, #6 |
| **C2** | **Proof lives in the issue, not the repository.** Don't commit evidence, receipt, custody or handoff JSON. CI proof is the PR's check link. Live proof is the closing comment: what changed, a command plus a one-line result, and remaining limits. Each issue gets at most one blocker note (a named decision or owner) and one closing comment. No progress checkpoints, workflow or pod UIDs, or local paths. | Evidence/custody practice |
| **C3** | **Squash before the first production migration.** No production database exists, so 0000–0002 become one slice-1 baseline with inline DDL. Each later slice issue owns its own migration, and the privacy CHECKs and views move with their tables. Once the first production migration has run, changes are expand/contract only (#74). | Frozen v1 + additive v2…v5 |
| **C4** | **Telemetry is as P4 says.** Keep the raw receipt plus the typed family tables with `ON CONFLICT`. Remove the conflict, integrity, loss-snapshot, provenance and transport layer. Fix the drain, spool, device seeding and rain. The hub source IP becomes optional (#73). | #12's reintroduced conflict model |
| **C5** | **The API is small.** Rebuild #14 by salvaging PR #59's sound parts: Pydantic models, signed cursors, the read-only SQL source, verify-full TLS and the generated TypeScript. Aggregates are computed on read with SQL `date_bin` plus coverage, and carry an evidence count or hash rather than ID lists. Remove the aggregate-revision store, the worker, the per-row evidence outbox and the publication clock. Default cadence policies ship in config. Code merges after #74 and doesn't wait for live data. | PR #59's evidence/revision system; #14 body |
| **C6** | **The web uses generated types.** `openapi-typescript` output from #14 is the only type source (P12), with lenient parsing of the fields the UI needs. History is aggregate-backed. Partial-coverage videos play with a coverage label. | Hand-written strict Zod contracts in #21 |
| **C7** | **Per-client edge limits.** Traefik `rateLimit` keys on `Cf-Connecting-Ip`. This is trustworthy because the origin is reachable only through the tunnel. Exempt hashed static assets. | Global bucket keyed per cloudflared pod |
| **C8** | **Collector fixes before cameras** (#75): canonical grid slot keys; an upper bound that doesn't depend on calibration; non-fatal protocol errors; backoff; drop the simplejpeg/NumPy decode, because FFmpeg in the renderer validates frames; fsync only at seal. | — |
| **C9** | **Owner asks go back to one PR each.** agents#5368: checksum-verified upstream `vlan`/`static` binaries placed by an init container or a guest-OS step, plus one plain NAD; no custom image, catalogue program or encryption. First verify whether the hub broadcasts to 255.255.255.255 or to the subnet, and size the NAD prefix to match. storage-infra#1107: two bucket rows, keys into KSOPS and custody rows, starting at ≤1024 GiB; the reader key can be reissued. cortex-ai-compute#402: closes on one HTTP 200 image call; drop the body-limit investigation. | Multi-PR owner programs |
| **C10** | **Slices are decoupled.** Slice-2/3 component issues are not blocked by #21, because the scaffold is merged. Slice-2 launch #49 depends on #6 and #21, not on the slice-1 launch #48. Code issues merge on fleet CI against ephemeral Postgres/Garage and deploy when their prerequisites are live. | Serialized graph |
| **C11** | **Requirement amendments.** See below. | — |
| **C12** | **The restore drill is split.** #6 proves database PITR. #77 proves the media/originals B2 custody restore right after #49. #33 keeps only the joint drill before #36. No PostgreSQL-vendor behaviour tests (deleted WAL segments, vector-count matching). | Monolithic #33 |
| **C13** | **Closeout is honest.** At #36, GC of orphans and tombstones runs with the grace guard. The expiry flags stay off; #79 enables them on its date. Add a minimal Grafana dashboard to #49's monitoring PR. #36 adds an upgrade runbook and Cortex key renewal. Keograms follow the stills lifecycle, and `keogram_bin` rows are deleted once the PNG is committed. | #36 vacuous expiry; lost WX-31/34 items |
| **C14** | **Remove the ceremony** (#76): evidence tooling, verifier scripts, commissioning probe policies, the rollout harness, stale contracts, ADRs and the generated backlog. Branch and worktree pruning needs Jason's explicit OK. | — |
| **C15** | **Right-size the database.** `weather-pg` starts near the options-pg pattern, about 250m/1Gi requests, and grows from observed working sets. | 1 CPU/2Gi ×3 |
| **C16** | **Use issue numbers.** Drop WX- keys from titles and new text; WX-n is issue n+1, which caused confusion. | WX keys |

## Requirement amendments (v4.2)

- **R01:** satisfied by #1 and the two re-plans.
- **R25:** not pursued; WX-27 was closed under P8. Cloudflare caching is a future option in D14.
- **R16 and R21:** "qualified" means the options-pg CNPG pattern (R16) and the Cortex scoped-key route (R21). It does not mean a separate qualification program.
- **§12.3 browser matrix:** narrowed to P19, which is Chromium, WebKit and Firefox in fleet CI plus Jason's real iPhone Safari check at slice 2. Resumed downloads are covered by #20's range tests.
- **§18.2 acceptance matrix:** superseded by the issue acceptance lists. #36 traces R01–R38 against issues, not against §18.2.
- **Migrations (P5):** an Argo **Sync** hook Job at wave 1, after the Cluster. Every DB-client workload runs at wave ≥2.
- **R26 failure tests:** a NAS-outage test means one safe live NAS-unavailability check at #49, plus fakes in unit tests.

## Critical path and order

**Slice 1 (#48).** These run in parallel now:
- #6: port the CNPG fix, right-size, commission, back up, run one switchover and the PITR drill
- #73: telemetry corrections
- #21: promote PR #72 without receipts, fix the rate limit
- agents#5368: plugins and NAD

Then #74, the baseline and first migration. Then #14, the API. Then #5, the receiver, which needs #74 and agents#5368. Then #48.

**Slice 2 (#49).** Can start now: #18 publication, #20 media origin and #75 collector fixes. Then #15 renderer, followed by #37, #38 and #39 in parallel, #40 and #19. #4 waits on **Jason's camera-source decision** (sentinel#550) and storage-infra#1107. #77 follows #49.

**Slice 3 (#50).** #41 events can start once #14's code merges. #25 search and #23 descriptions follow. Then #27, #44 and #78. #33 runs before #36. #42 almanac and #45 card can land any time after #14.

**Closeout:** #36, then wordpress#242 and #79. Non-blocking throughout: #76, #16, #46, #47.

## Decisions for Jason

1. **Camera source (blocks slice 2).** The view-only camera user gets 403 on `snapshot.cgi`, and the firmware has no snapshot-only grant. The options:
   - (a) give a dedicated weather user the minimal extra right that permits snapshots, tested on `frontyard_1` first
   - (b) take native JPEGs from a Sentinel-provided path, such as a go2rtc frame endpoint or a one-frame RTSP grab, accepting D02 source-time changes
   - (c) accept a different resolution
2. **Interpolation pilot (#16, non-blocking).** Approve `weather-pilot-*` Jobs in `weather` that mount staging read-only and write to a disposable PVC (the restore-drill pattern), or keep the pilot out of the production namespace.
3. **Cleanup.** OK to delete superseded and `archive/*` branches and stale worktrees (#76).
4. **Later, already listed:** iPhone Safari at slice 2; capture cadence × shutter cost tradeoff at #4; third copy and CSV at #36.

## Working method from here

- **Issue body** (aim for under 2 KB):
  - Outcome (1–2 lines)
  - Acceptance (at most about 10 checkboxes, each one a command or an observable behaviour)
  - Blocked by (native links only)
  - Not in scope
- **Definition of done:** as in [AGENTS.md](../../AGENTS.md), read through C2. The PR's fleet check link is the CI proof. "Deployed" means a `deploy:` pin, Argo Synced+Healthy and the running digest.
- **Execution:** Claude Code sessions on the Mac edit and orchestrate. Builds, integration suites, Playwright and media work go through fleet CI or `weather` workloads, never the Mac.
- **Branches:** one worktree per active PR; delete the branch on merge.
