# Product configuration and private policy binding

Status: public product-settings schema selected; complete private bundle formats and numeric admission remain open under their ADRs. Implements requirements §§16.3 and 17.1. Consumers WX-01/03/08/09/10/18/31/34. [configuration.schema.json](configuration.schema.json) covers all categories in the §17.1 example; it is not a Kubernetes manifest, secret format, deployment grant or complete private startup configuration.

## Fixed requirements and commissioning

Validate strict JSON-compatible settings with unknown keys rejected. Parse YAML, if used, without custom tags, duplicate keys or implicit non-JSON values; reject nonfinite numbers before schema validation. No silent environment-variable/default fallback may bypass the validated effective settings. Resolve environment substitutions before validation without logging their values. Public examples contain synthetic references and are never production admission.

The schema fixes selected product invariants: the three cameras, civil-twilight definition, cycle budget/60 fps, snapshot/NFS capture, private original bytes and required FKs, same-origin single canonical Garage bucket, complete received telemetry, local history/astronomy, CPU fallback, completed-video asynchronous analysis, exact-search baseline and accepted initial/follow-on product scope. Optional 8x is a declared recipe candidate, not enabled or qualified merely by its presence.

Commissioning fields remain adjustable only with a new admitted policy revision: origin/timezone; dawn/dusk offsets; capture cadence, deadline and chunk length; hourly retention; preview/UI refresh and preview embargo; shutter angle. The schema enforces positive sizes/intervals, legal angle and a minimum 600-second embargo. It does not approve extreme values within a numeric type range. Runtime policy validates safe operational limits, temporal geometry and selected defaults. Offset changes must preserve shared neighboring boundaries and A < B < C; they never introduce independent overlap. A timezone must resolve in the pinned tzdata version; a syntactically plausible string is insufficient.

The initial values in §17.1 remain commissioning defaults, not silent production defaults. Five-year hourly retention is independent of video retention. Reducing retention cannot trigger deletion until the selected recovery/lifecycle policy explicitly permits it. Configuration validation never grants permission to weaken complete telemetry preservation or expose private originals.

## Replace unresolved placeholders with bound policies

The requirements' illustrative preview-retention, keogram-region and embedding-origin placeholder strings are not accepted runtime values. They become immutable `{id, sha256}` references to private policies. Four additional required bindings identify commissioning, private deployment, retention/recovery and resource admission. A hash binds bytes; it does not prove owner authority, suitability or existence.

The private deployment bundle must resolve actual endpoint/credential projections, location, per-camera source/timing/privacy/region configuration, exact namespace/PVC/database/bucket/role identity, image/extension/profile versions and owner admission receipts. It is not included in public fixtures and no credential value is permitted as an extra public setting. Separate per-role projections prevent collectors, public API, image workers and cleanup from receiving unrelated secrets or authority. Detailed private bundle schemas remain required design work; references alone do not close §17.

Preview policy resolves rolling retention, publication-age proof, expiry, bounded queue and withdrawal. Keogram policy resolves per-camera versioned regions/bins and quality/privacy rules. Embed policy resolves the precise approved parent origins and card-only CSP. Resource policy resolves numeric queues/concurrency/CPU/memory/storage/free-space/deadlines and placement. Recovery policy resolves class-specific lifecycle, source/promotion pins and retained checkpoint dependencies. Each policy references relevant qualified ADR/owner receipts and exact configuration versions; strings such as `selected_in_D16` or arbitrary source hashes are not proof.

## Startup and change acceptance

1. Parse, validate and compute a deterministic effective configuration revision without exposing private values.
2. Resolve every reference from approved custody; hash-check bytes, schema/version and ownership, then verify binding to this namespace/workload/role and the intended environment.
3. Verify all cross-field invariants, admission limits, dependency versions, actual mounted identities and relevant qualification receipts. Reject synthetic/placeholder references and `.invalid` endpoints in production. Do not treat a generic image or another tenant's healthy resource as admission.
4. Start each role only when its mandatory settings are valid. Invalid collector timing/privacy/grant configuration cannot silently initiate capture; invalid cleanup retention/recovery configuration cannot initiate deletion. Existing safely admitted roles continue only under their own recorded valid configuration and failure policy.
5. Freeze schedule/capture/render/profile revisions in newly admitted work. A config reload cannot rewrite identities or deadlines of running/sealed work. Use explicit drain/rollout or revisioned replacement; retain the prior validated configuration and compatible rollback coordinates.

Wrong credentials, endpoint redirects and dependency unavailability are operational failures, not invitations to try public or less restricted alternatives. Redact diagnostics to field/policy ID and reason; do not print full resolved configuration, secrets, coordinates or raw source URLs. Startup may expose sanitized readiness failure while refusing unsafe work.

## Verification and limits

[Thirteen shape fixtures](../archive/planning-2026-10/contracts/configuration-fixtures.json) cover complete synthetic settings and rejected secret fields, missing bindings, weakened privacy/embargo/CPU/telemetry invariants, placeholder retention, wrong output rate, second bucket, zero cadence and unknown keys. They do not resolve private policies, enforce admission or test startup.

WX-08/34 must add semantic cases for mismatched policy hashes, absent/forged owner custody, another tenant's grant, unresolvable timezone, invalid shifted twilight ordering, zero/free-space exhaustion, unsafe operational bounds and stale policy rollback. WX-03/10 qualify real staging/camera bindings; WX-18 tests retention and pin safety; WX-31 reports sanitized failure. No test may depend on real credentials in untrusted CI. Whole private configuration coverage, exact capacity/lifecycle values and live role startup remain unverified.

## Selected preview policy format

The [preview policy schema and contract](preview-policy-contract.md) now resolve `camera_previews.retention` for the selected D16 v1 behavior, including three distinct time decisions and cross-binding equality with public settings. Twelve synthetic fixtures check shape only. Other private policy formats, real reference resolution and production admission remain incomplete.
