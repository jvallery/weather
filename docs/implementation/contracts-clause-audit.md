# Configuration and transport contract requirements audit

The [source-bound ledger](contracts-clause-audit.json) preserves all section 17 prose, the complete YAML example and every sidecar field group. Acceptance below distinguishes schema shape, cross-record semantics and actual resolved private policy. No schema-valid example is a deployment grant.

## §17 — WX-01, WX-08, WX-17, WX-33

- Finalize machine-readable schemas/OpenAPI and migration contracts before implementation; illustrative shapes are not production configuration or closure of unresolved ADRs.

## §17.1 — WX-01, WX-03, WX-08, WX-09, WX-10, WX-18, WX-34

- Preserve every category and selected invariant in the complete YAML example: site/cameras/time/capture/hourly/previews/keograms/render/weather/media/backup/analysis/search/experience.
- Distinguish fixed product scope from admitted commissioning overrides; the three cameras, CPU fallback, private originals, exact FKs, single bucket, same-origin media, complete telemetry, local history and follow-on/deferred scope cannot silently change.
- Resolve private location/endpoints/credentials and exact PVC/cluster/bucket/role/GPU/version/resource/retention bindings under their ADR/owner policies, with hashes and authority checks; references alone do not constitute complete deployment configuration.
- Validate effective settings at startup, reject placeholders/unsafe defaults and refuse affected role work when policy resolution fails. Preserve sealed work revisions on config changes.

## §17.2 — WX-13, WX-16, WX-17, WX-22, WX-24, WX-36, WX-37, WX-43

- Published/partial/unavailable sidecars represent real state with nullable absent assets; never fabricate MP4s for scheduled segments.
- Identity binds schema/video/camera/cycle/segment, media/metadata revision, state and update time; temporal fields include UTC and offset labels/timezone, twilight version, frames/fps/rational duration, authoritative source map and uncertainty.
- Capture fields preserve expected/accepted slots, cadence epochs, gaps, time basis, exposure coverage and processing caveats without private source credentials/addresses.
- Media/poster fields expose same-origin immutable identities, MIME/codec/profile/level, geometry/fps/bytes/hash, poster PTS/source interval and native download; related keogram/status/UTC map, day/hour public still and event links remain resolvable.
- Weather binds per-metric values/units/coverage/evidence, aggregate computation algorithm and input revision plus missing reasons; never conflate value algorithm with coverage algorithm.
- Analysis binds state/mode, validated claims/events/evidence, precision/coverage/limitations and safe prompt/schema/capability provenance; one unsupported prose string is insufficient.
- Editorial projection exposes effective public revision, human attribution flag/time, public tags/poster choice; exclude private actor audit, notes and favorites. Search exposes index/profile/generation/current metadata revision, not vectors/private serving details.
- Integrity binds input manifest, recipe/build, immutable objects and publication generation; sidecar hash belongs in external DB/object receipt, never self-referential bytes.
- Validate before export and serve the same canonical API revision; deny station serial/private location/raw URL/token/internal GPU placement/unsanitized response in every public projection.

## §17.3 — WX-13, WX-25, WX-26

- Current response separates generated_at from per-metric observed/received/cadence/valid-until/quality/stale reason and sanitized station alias.
- History binds requested interval/resolution/cursor/support/gaps; missing remains distinct from zero.
- Search retains ranking/profile/filters/count/metadata and evidence precision independently of nullable seek; cosine similarity is never confidence percentage. API timestamps are UTC; local labels are supplemental.

## §17.4 — WX-17, WX-36, WX-37, WX-38, WX-40, WX-41, WX-42, WX-43

- Images distinguish calendar/cycle dates and bind camera/day/slot, offset label, UTC support/uncertainty, selection offset/policy, state/reason/revision and public derivative URLs only.
- Private final archive receipt binds original hash/bytes/geometry, enforced day/hour/source FKs and durability/retention policy; never serialize it through public APIs. Compact staging promotion is not final Garage archival success.
- Preview includes capture bounds, embargo, actual first publication, expected refresh, expiry and state; origin enforces eligibility independently of cached listing.
- Keogram binds segment, exact UTC bins, observed lineage/coverage and image recipe; public digest must resolve to qualified private lineage without exposing original credentials.
- Events bind origin, interval/precision, evidence/rule revision, linked calendar days and valid seeks. Almanac binds calendar bounds, units, formula/library/history/coverage/missing reasons; numeric matches state actual statistic/window/coverage.
- Editorial writes carry expected effective revision and bounded allowed fields; server authorization/audit chooses actor, never caller-supplied identity.

## Findings and remaining closure

The comparison found and corrected missing computation identity in `Aggregate.algorithm_revision`; the previous search pass added independent `SearchHit.evidence_precision` for nullable seek. Current [60-fixture validation](../contracts/aggregate-provenance-validation.json) proves shape expectations only. Neither fixture count nor field presence proves source-time correctness, evidence membership, current revision, privacy or actual formula execution.

Configuration covers the public product categories with private policy references. Preview policy has a selected format; complete private deployment/resource/clock/retention and per-camera region bindings remain unfinished. The final hourly-original Garage archive/FK receipt is also outstanding: compact promotion records only transfer to protected staging. These are planning gaps, not implementation evidence waived by this mapping.

Sidecar `keogram_id`, day/still/event IDs and versioned recipe identities require coherent resolvers to the corresponding contracts; IDs alone cannot prove the related product exists at that revision. Integrated tests must compare API/export revisions and deny stale/private aliases. Migration semantics remain tied to the selected database design and expand/contract policy. Existing native graph edges are unchanged.
