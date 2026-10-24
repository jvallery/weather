# Section 5 time-model audit

Scope: every prose paragraph and fenced example in §§5.1–5.5, retained exactly in the [source-bound ledger](time-clause-audit.json). This is grouped design/acceptance traceability, not a claim of running implementation tests or complete whole-document audit. All runtime acceptance is unproven.

The API schema checks representation; strict interval ordering, cross-record membership, exact source mapping and FK relationships require semantic validation. Decimal seek seconds are a browser transport convenience: calculate ownership with integer/rational source/frame arithmetic first, then serialize. Never infer frame membership by round-tripping a floating-point seek value. A request at an exclusive segment end cannot silently become a sample owned by that segment; clamp only as an explicitly labeled UI seek operation to the final owned frame.

## §5.1 — WX-09

- Private location/config hash and pinned tzdata/astronomy; no HA runtime dependency or public precise coordinates.
- Explicit civil depression 6 and unobstructed horizon; sunrise/sunset display distinct; do not treat elevation above sea level as observer height.
- Freeze A/B/C from local date and next local date, half-open shared boundaries, UTC elapsed arithmetic and offset labels.
- Zero offsets default; shifted shared boundaries must preserve strict ordering; invalid/no solar event yields schedule error, never substitute midnight/sunrise.

## §5.2 — WX-17

- Cycle date is local date of dawn; overnight label spans dates while belonging to the first cycle page.
- Dusk/dawn plus seal grace triggers publication independently; no midnight wait; invalidate projections and run repair reconciliation.
- All six scheduled records accounted for with honest states and independent camera progress.

## §5.3 — WX-09

- Exactly 7200 frames across both segments, rational half-up daytime allocation and exact remainder; reject zero-frame segment.
- Exact frame centers and output PTS; all three cameras share allocation and gaps do not compress time.
- No title/audio duration extension, no 59.94 fps; both timescales 60000, terminal sample and container duration checked.
- Do not hardcode 12-second step; derive from actual solar interval and selected budget.

## §5.4 — WX-10

- B belongs only to overnight; C only to next daytime; capture IDs cannot occur in two segment manifests.
- All temporal context stays inside assigned segment; synthetic edge extension is declared and bounded (D10 v1 disables it).
- Assigned interval disjointness is not physical exposure precision; retain uncertainty and record gaps in strict boundary mode.

## §5.5 — WX-08

- weather_day has stable unique site/date identity, timezone epoch and UTC midnight bounds, distinct from solar cycle.
- Elapsed local hour slots keyed by UTC with offsets/fold; 23/24/25 slots and only one active selection per camera/slot.
- Missing/ineligible slots explicit; no another-hour substitution; source capture time selects parent day, not upload/cycle date.
- Enforce camera/day/hour/capture/segment relationships and same-day slot FK; retain compact identities after raw cleanup.
- Join solar/calendar products by UTC overlap; navigation uses UTC intervals and visible gaps with disambiguating local labels.

## Producer and consumer checks

WX-09 owns schedule/allocation, WX-08 calendar/FK structure, WX-10 capture ownership, WX-17 publication clocks, WX-16 exact mux timing, WX-37 hour selection and WX-39/40 navigation. WX-21 clip partitioning must preserve this same map; no independent time model. WX-33 exercises the contracts together. Existing D02/D10, capture/publication and database-migration designs provide approaches; private location/library pins, real camera time evidence and runtime qualification remain open.

[Exact expected-value vectors](../../../contracts/time-model-vectors.json) cover four allocations, first/second/last frame centers, and Denver spring/fall/ordinary day slots (23/25/24). The half-up case is deliberately synthetic, not an astronomical claim. A preliminary day=1/night=15999 case produced zero daytime frames; it is retained as a rejection obligation, and day=1/night=14399 tests the exact half-up boundary. These vectors were calculated using rational arithmetic and host timezone data, not the future application. Production must pin timezone/astronomy data and verify against these fixtures rather than inherit the host implicitly.

Remaining tests include shifted/invalid/missing solar events, hidden precise location in serializers, six per-camera outcomes with one outage, observed uncertainty crossing boundaries, cross-day FK rejection, all temporal filters refusing adjacent-segment context, failed playback seek boundaries and projection/reconciliation races. Passing the small arithmetic check proves neither astronomical correctness nor publication/media behavior.
