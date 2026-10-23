# Semantic and measured search audit

The [source-bound ledger](search-clause-audit.json) preserves every section 14 source block, including the exact query transformation, canonical document illustration and SQL index example. Producers own the complete grouped obligations below; WX-33 integrates them. No model, database query or index build ran during this pass.

## §14.1 — WX-23, WX-25

- Combine recorded video metadata/validated descriptions with semantic, lexical and exact typed filters; search does not answer new weather questions or generate SQL.
- Requalify embed.main route/model/weights/serving, expected 2560 dimensions, pooling, normalization, limits and latency/errors with scoped Weather access; historical candidate metadata is not live acceptance.
- Bind exact UTF-8 query prefix concatenation and unprefixed documents in one immutable profile fingerprint, including model, pooling, similarity, normalization and serving provenance. Same dimensions never authorize model substitution.
- Validate output count, dimensions, finite components, nonzero norms and qualified tolerance; profile changes require evaluation rather than silently changing transforms.

## §14.2 — WX-24, WX-32, WX-43

- Build bounded deterministic public whole-video and useful evidence-window/event documents, never every synthetic frame; retain all example text fields and separate instrument provenance/units.
- Bind video/media/analysis/editorial/measured revisions, camera, source/seek/evidence/mode/quality, text hash and profile/generation; typed SQL fields handle dates/camera rather than embedded timestamps.
- Use durable outbox idempotence on document/text/profile with finite retry/deadline and current-revision CAS; ingestion/publication proceed without embeddings and pending videos remain browsable.
- Withdraw incorrect text immediately while replacement is pending, including late-weather dependent synthesis; unchanged independently valid visual text may remain.
- Index effective public human revisions with attribution, excluding favorites/notes/operator identity/private-original metadata. Future visual research stays separate from text profile and does not require wholesale raw retention.
- Shadow-build and evaluate changed profiles, atomically activate and retain bounded rollback; back up original vectors/profiles for inference-independent restore and never rank incompatible spaces together.

## §14.3 — WX-23, WX-25

- Store expected full-precision vector(2560) on qualified pgvector; conventional vector ANN limit prevents a direct 2560-dimensional HNSW/IVFFlat index. Never truncate dimensions.
- Start exact filtered search; measure actual window/event document count and query cost before adding ANN. Six daily videos alone understates corpus size.
- Optional halfvec expression HNSW requires compatible installed version, matching query cast, profile/generation isolation, bounded candidate count and full-precision reranking.
- Treat SQL example as index-shape illustration, not complete public query. Qualify memory, filtered recall and underfill; bounded fallback never removes required predicates and similarity is not probability.

## §14.4 — WX-25, WX-26, WX-32

- Enforce bounded 512-character text, top_k<=20, explicit/default bounded archive interval and admitted candidates; parameterize SQL and enforce active generation/public/revision eligibility inside candidates.
- Use strict half-open overlap source_end>request_start AND source_start<request_end, including exact-touch negative cases.
- Return snippet/camera/source interval/evidence precision/mode/quality/media revision/poster/validated seek/ranking mode; collapse videos deterministically while retaining optional supported windows. No user-generated database operations.
- Rate-limit embedding separately from SQL and cap concurrency; bounded query cache binds exact transformed bytes and full profile. Outage preserves labeled lexical/browsing and exact-profile cached semantics only, without external/model substitution.
- Evaluate clear/cloud/rain/snow/night/obstructed/partial and negative queries across cameras/date filters; compare optional ANN to exact, measure relevance/false matches and validate every seek.
- Restore metadata and full vectors/profiles with inference blocked; verify stored-vector exact plus typed/lexical operation. Existing vectors alone do not imply arbitrary fresh query embedding is available.

## §14.5 — WX-13, WX-25, WX-26, WX-42

- Version numeric metric/statistic/operator/value/unit/scope/minimum-coverage schema, convert units deterministically and parameterize SQL; UI names the statistic/window. No AI-derived numeric truth or arbitrary formulas.
- Default numeric scope is exact whole-video source segment aggregate revision; clip matches label the broader measured condition, with narrower scope only after qualified support/precision. Civil days and solar cycles stay distinct.
- Return matched values/units/interval/coverage/revision. Unknown or low coverage never satisfies zero/no-rain or ordinary comparisons.
- Apply visibility/camera/time/numeric predicates before ranking and through ANN/exact/lexical fallback; empty text enables model-free measured/date/camera browsing.
- Cache identities include complete filter schema and aggregate revision; corrected evidence invalidates results. Test unit equivalence, zero/empty/incomplete rain, sparse gusts, late data, shared station, DST/overnight, overlap, underfill and outages without relaxing truth to fill top_k.

## Contract defect corrected

The API draft previously carried evidence precision only within nullable `seek`. A nonplayable result could therefore lose required §14.4 precision. `SearchHit.evidence_precision` now preserves frame/interval/clip/segment scope independently of playback. The [API contract](../contracts/api-contract.md) requires semantic agreement with any seek and separately labels whole-segment matched measurements. Five new fixtures cover null-seek clip, absent/invalid precision, measured-only segment and playable clip; the [receipt](../contracts/search-precision-validation.json) verifies all 57 API shape cases with explicit date/UTC checks. It does not prove evidence resolution or cross-field equality.

## Evidence and remaining planning gaps

D12 selects exact filtered search, deterministic fusion, evidence-current revision checks and shadow activation. E05 establishes available extension packaging, not Weather installation/performance. Current model/weight/backend correlation, scoped access, normalization, canonical-document token policy, admitted resource/enforcement budgets and relevance thresholds remain unresolved. D12 now selects explicit English plain-text lexical ranking and protective request/SQL/model-wait/cache ceilings; actual capacity, distributed enforcement and parser behavior still require qualification. OWN-MODELS and OWN-DB are existing prerequisites. Inference-free restore means stored-vector comparison and typed/lexical queries work; it does not promise embedding arbitrary new text without a model or matching cached vector.

The earlier integrity vectors remain declarative expectations. Actual filtered recall, correction races, cache invalidation and restored behavior require implementation proof; no new graph edge is needed for this acceptance mapping.
