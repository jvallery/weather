# Synthesis candidate, grounding and public attribution

Status: private Stage C boundary selected; actual route qualification and D11 execution budgets remain open. Implements requirements §§13.6–13.7, 12.7 and 14.2. Consumers WX-21/22/24/40/43; integrated acceptance WX-33. [weather-narrative.schema.json](weather-narrative.schema.json) validates untrusted candidate shape, not factual truth. It is separate from both the Stage A visual schema and public API records.

## Exact request and candidate boundaries

The [synthesis system prompt](prompts/narrative-system.txt) and [visual system prompt](prompts/visual-system.txt) are exact extracted templates from requirements §§13.6 and 13.4. Version their bytes with every job. Substitute only application-validated context; evidence descriptions and pixels never supply instructions, tool authorization or schema changes. No inference request was made to create these artifacts.

A candidate has only schema_version, summary_claims, events and limitations. Claims use text/basis/evidence IDs; events use title/category/basis/evidence IDs/categorical confidence. Basis is visual, instrument or inference. Six claims, eight events and eight limitations are maxima, not quotas. Empty claims/events are valid when no supported content exists; do not pad to the word target. Text/title/limitation strings have a 240-character protective ceiling. Each citation list has 1–100 unique IDs, matching the public record ceiling; a larger evidence set must be split or deterministically aggregated under a qualified recipe, never silently truncated.

Category is a bounded identifier whose membership is validated against the request's versioned category registry. Initial supported visual categories are those of §13.5; measured-event categories additionally require the deterministic event rules in D05/D16. The model cannot register a new category. Schema-valid arbitrary category text is not eligible for publication.

Candidates contain no UTC interval, output seek, actor, public ID, URI, measured-value mutation or free-standing summary field. The application assigns those from current evidence and validated projections. Schema validation rejects extra fields, but plain-text/markup and evidence validation remain separate. Render the final summary from accepted claims; no untraceable second prose string becomes authoritative.

## Grounding and projection

Validate the request-bound media hash, capability/schema/prompt revisions, weather evidence revision and editorial baseline. Resolve every cited ID to the exact supplied validated observation or deterministic instrument record. Reject unknown/stale IDs even when they have the right syntax. A citation to an irrelevant true measurement does not ground the associated text.

Visual basis requires actual visual support, preserves assessment/confidence/limitations, and cannot establish absent weather over unreviewed time. Instrument basis requires instrument support; numerical value, sign, statistic, unit and interval must match the supplied record. The model performs no conversion or aggregation. Prefer application-rendered deterministic numeric templates; if a free-text numeric assertion cannot be reliably matched, replace that claim with the verified template or omit it. Merely finding the same numeral somewhere in the evidence is insufficient.

Inference basis marks a supported interpretation, not permission to fabricate measurements, forecasts, personal activity or fine temporal detail. Preserve disagreements between visual and measured evidence as separate claims. Never promote `possible` or `not_assessable` evidence into a confident observed fact. Reject executable markup/instructions and out-of-scope personal claims; escaping unsafe text does not make it a grounded weather observation.

Resolve event support from the cited evidence intervals. Do not bridge disjoint windows into a continuous occurrence without a qualified continuity rule: split the event or explicitly label intermittent support while retaining gaps/precision. If splitting would exceed eight events, select supported events deterministically under the versioned policy or retain fewer; never merge intervals just to fit the schema. Clip evidence does not acquire frame precision. Every resulting seek is inside the owned published media interval.

Public Claim and Event now carry a required `basis` independently of producer `origin`. Automatic visual records use origin visual/basis visual; deterministic measured records use measured/instrument; inferred synthesis uses synthesis/inference. Human Claim and manual Event origins preserve their explicit evidence basis and human attribution; they do not relabel themselves as untouched AI or deterministic measurements. All public reads, sidecars and search text must agree on this attribution. The private categorical event confidence is preserved in provenance and expressed without false precision in the public text/limitations; it is not a numeric probability.

## Commit, failures and continuity

Only validated claims enter the publication CAS. Compare current media, measured evidence and editorial revisions again before committing metadata/outbox changes. Keep the immutable candidate/validation receipt privately under the bounded diagnostic lifecycle; do not expose raw JSON/error bodies to visitors. An automatic analysis update cannot overwrite an active human correction against unchanged evidence.

An HTTP 200, valid schema or nonempty output is not success. Reject truncated generation, unsupported media, stale context and semantic failures. Repair/retry uses the existing overall/window call budgets and does not release the global inference slot while server termination remains uncertain. If synthesis cannot be validated, publish the deterministic measured summary plus independently validated visual observations, with honest degraded/coverage state. Do not fail capture or media publication to wait for prose.

[Shape fixtures](weather-narrative-fixtures.json) cover schema limits and separation. The semantic scenarios there are declarations for WX-22/33, not passing tests. Actual fresh temporal video controls, request limits, cancellation/termination, numeric-grounding implementation, event-rule taxonomy/continuity and retention budgets remain D11/D16 qualification; this contract does not close those decisions.
