# D11 — Bounded video analysis, uncertainty and publication

Status: job/slot/publication protocol selected; actual native-video capability and backend termination remain material OWN-MODELS qualification. Requirements §§13.1–13.7, 12.7 and 14.2; consumers WX-21/22/24/31/33/40/43. This design introduces no live model request or server setting.

## Inputs and qualification

Use the owner-qualified `llm.primary.longctx.mm` route; never substitute the language-only sibling or external service. Qualify fresh forward/reversed synthetic video with a nonce, a real completed-video derivative, supported request/media parameters, actual sampled positions, complete schema-valid output and backend termination. Historical image success and source adapter files are clues, not native-video proof. Sampled frames extracted from completed MP4 remain an explicitly degraded path and cannot close native-video commissioning.

Freeze video ID/media revision/hash, exact rational clip/source mapping, prompt/schema/capability revisions and supplied evidence IDs in every job. Stage A sees visual context only. Stage B calculates measured aggregates deterministically. Stage C uses validated A plus versioned B, under the [synthesis contract](../contracts/weather-narrative-contract.md). Original captures are not a substitute for completed-video input and analysis does not extend dense capture retention.

Partition source segments into consecutive at-most-one-hour windows with the §13.2 rational frame-center rule. Before sampling, windows cover exactly `[0,N)` without overlap/omission; do not hardcode 24 windows or use imprecise keyframe-only cuts. Bound proxies by the smaller qualified application/route/backend limits (initial proposals: 8 MiB clip, 16 MiB serialized request, 768–960 px width, 24–32 temporal samples). Record actual sampling and uncertainty; unsupported sampler observability is not proof that every requested frame was consumed.

## Durable request ownership

One global Weather analysis slot covers Stage A, Stage C, repairs and refreshes on this route. The coordinator persists the active call identity, owning epoch, request hash, run/window IDs, dispatch state and termination evidence. Workers never free the slot simply by losing their lease or restarting. Embeddings have a separately qualified budget under D12; this slot is not permission to ignore shared owner admission.

| Call state | Allowed next step |
|---|---|
| Reserved, not dispatched | Persist the exact attempt/count and ownership before send; expired undispatched work may release only with evidence it could not send. |
| Dispatching / active | Send at most once under that identity; retain slot until completion or qualified termination. Record accepted request identity where supported. |
| Completed | Validate full response and finish reason; release execution occupancy separately from acceptance/publication. Invalid content may consume a bounded repair attempt. |
| Timed out / connection lost / worker lost | Mark execution uncertain and open the circuit. Do not retry, dispatch another analysis or assume the provider stopped. |
| Cancellation requested | Still occupied; a sent cancellation is not termination evidence. |
| Terminated | Release only after owner-qualified acknowledgement/status or a proven finite server termination bound applicable to that exact request and route version. |
| Unresolvable | Preserve uncertain occupancy, fail/degrade affected runs at their deadlines and require owner reconciliation. Never clear it by an arbitrary local timer. |

A transaction commit acknowledgement lost before dispatch is reconciled by durable identity before action. If send outcome is unknown, treat it as possibly sent. Provider idempotency may be used only if qualified; an application idempotency key alone cannot prevent duplicate remote generations. Coordinator failover must retain the uncertain call and suppress new dispatch. Termination qualification includes the broker/adapter and actual backend, not just client cancellation.

## Budgets and fair scheduling

Adopt the requirements' commissioning bounds as persisted policy: one active analysis call; 120 seconds per attempt; at most two transient retries plus one repair, with at most four actual calls per window; ten minutes per window from first execution; initial run deadline publication plus four hours; 256 pending windows globally. All waiting after start, backoff, retry and repair consume the same window/run budgets. Queue wait always consumes the run budget. Reserve input/media plus the admitted output allowance (initial 1,200–2,048 tokens) within the real context limit before dispatch.

Use round-robin camera selection among eligible work, oldest run first within a camera, skipping backoff/expired work. Recheck deadlines and call counters atomically before each dispatch. Unsupported media or invalid authentication stops retries until configuration/capability changes; throttling honors bounded backoff. Budget exhaustion preserves validated successful windows and explicit missing coverage. A terminal run never reopens because another packet or queue delivery arrives.

Stage C is a distinct bounded job under the same slot. Selected commissioning ceiling: four total calls including retries/one repair and ten minutes from first start, always capped by its run deadline. Allow at most one pending synthesis/refresh per video; proposed global synthesis queue ceiling 256, separate from the window queue. These ceilings require owner/D16 resource validation and cannot extend a video's run deadline. If no time remains, use deterministic measured text plus accepted visual observations instead of dispatching late work.

Automatic full-video reanalysis can create at most one new run per video in a rolling 24 hours, only within seven days of publication. Enforce admission transactionally across schedulers. Its deadline is `min(created_at + 4 hours, publication_at + 7 days)`; old run identity/deadline remain unchanged. Explicit later archival jobs have a separate admitted policy and do not bypass the shared slot.

A weather-only correction immediately invalidates affected synthesis/search claims, keeps valid visual observations, and coalesces one pending synthesis refresh. Selected debounce proposal: 60 seconds since the newest revision with a five-minute maximum wait from the first pending correction; sustained updates therefore cannot postpone work forever. Each started refresh binds its chosen revision and has a ten-minute/four-call ceiling, subject to the shared slot and queue admission. New changes during execution create/coalesce the next pending refresh; stale CAS results cannot publish. These commissioning proposals remain unmeasured.

## Publication state and recovery

Model-output validity, execution outcome and published data availability are separate. Public Analysis retains `state` for pending/running/complete/partial/unavailable/invalidated data and adds `execution_state` for pending/running/complete/degraded/failed/unsupported, plus a sanitized reason. A failed run may leave usable partial validated evidence. An invalidated prior summary may coexist with an earlier completed run; it remains excluded from current claims/search. Sampled-frame fallback is degraded with its true mode/coverage, never silently native-video complete.

Map failed/unsupported with no usable evidence to unavailable data; retain execution state and reason so visitors understand the limitation. Map successful partial support to partial data with honest coverage. An empty but valid assessment of unassessable footage cannot claim complete visual coverage merely because execution finished. These cross-field rules require semantic tests, not just enums.

Publication CAS checks media/hash, measured evidence and editorial baselines after validation; atomically commit metadata, invalidate superseded documents and emit outbox work. Late results may be historical but never replace current incompatible evidence or active human corrections. No inference failure blocks capture, deterministic measurements, durable media publication or independent recovery. Restore clears/reconciles in-flight remote execution before enabling dispatch; restoring the DB does not prove a previously sent provider call stopped.

## Acceptance and unresolved closure

[Execution vectors](../contracts/analysis-execution-vectors.json) are required synthetic/integration scenarios, not executed tests. WX-21 proves actual native-video and server-termination behavior; WX-22 implements budgets, recovery and CAS; WX-31 observes pending, uncertain occupancy, deadline/queue overflow and partial coverage distinctly. WX-33/40/43 verify public states and evidence/editorial races.

Remaining: actual route/model/backend/request provenance, scoped Weather access, observed termination mechanism, admitted resource/token/deadline limits, final debounce/diagnostic retention, event taxonomy/continuity rules and real temporal controls. Keep D11 open; do not resolve these by claiming safe fallback meets native-video acceptance or by tuning shared servers during planning.
