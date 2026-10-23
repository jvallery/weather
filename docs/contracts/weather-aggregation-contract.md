# Deterministic weather summaries and wind roses

Requirements §§10.5–10.6; D05/D12/D16; producers WX-13/41/42, consumers WX-22/39/40. This specifies calculations over already validated, deduplicated metric support. Raw parsing/support calibration remains D05 qualification; no observed fixture is invented from a packet receipt timestamp.

## Common support and revision rules

Intersect each metric's qualified half-open support with the requested UTC interval. Resolve conflicts before aggregation; overlapping competing values cannot be averaged as independent time. Coverage is the union of eligible support duration divided by elapsed window duration. Preserve missing intervals and algorithm/config revision. Count-based receipt coverage is a different diagnostic.

Temperature/humidity means are sum(value × supported seconds) / supported seconds, with null and reason when duration is zero; min/max are observed eligible values, not interpolated extrema. Apply the effective per-metric hold cap, never a universal unlimited hold. Preserve exact source precision internally and round only display output. Calendar-day aggregates use real UTC day bounds, including DST duration; video summaries use their own solar interval.

Precipitation sums reconciled unique amounts over qualified protocol support. A boundary-straddling report can be split by overlap fraction only with the uniform-intensity estimate labeled and exact conservation across adjacent windows. Unknown support is excluded from qualified totals with a reason. Missing time is never extrapolated to full-period rain. Lightning uses one configured event/count authority, keeps integer counts and exposes boundary-uncertain counts separately; do not add events and interval counts or split a strike fractionally. Instrument precipitation classification remains distinct from visual claims and is not snow depth.

Solar energy is sum(irradiance W/m² × valid seconds) / 3600, in Wh/m². Pressure remains station pressure. Pressure trend uses duration-weighted endpoint-window averages with coverage and their actual midpoint separation; window width/minimum support is versioned and still requires commissioning. No implicit sea-level conversion. Evidence IDs bind metric/statistic/window/unit/algorithm/coverage and revision; corrections invalidate dependent synthesis, records and search through outbox/CAS while retaining prior provenance.

## Wind recipe v1

Selected commissioning defaults: calm speed <0.5 m/s; weak normalized resultant <0.1 means variable mean direction. These are explicit product policies to qualify against instrument behavior, not meteorological certainty. Validity flags and per-metric hold limits still take precedence. Record the chosen thresholds in algorithm provenance.

Mean speed is duration weighted over valid speed support. Maximum gust is the maximum eligible reported gust, not a maximum reconstructed from averages. Direction uses the intersection of valid speed and direction support excluding calm/invalid direction. With meteorological *from* angle theta clockwise from north, accumulate E = sum(speed × seconds × sin(theta)), N = sum(speed × seconds × cos(theta)). Direction is atan2(E,N) normalized into [0,360). R = hypot(E,N) / sum(speed × seconds). Empty denominator yields no direction; R <0.1 yields variable, not an arbitrary north angle. Do not average 359 and 1 into 180. Direction coverage and speed coverage differ and must be reported separately.

The wind rose is a duration histogram, not the mean direction. Use 16 sectors centered on compass directions at 22.5-degree spacing, with half-open edges and north wrapping [348.75,360) union [0,11.25). Initial directional speed bins in m/s are [0.5,2), [2,5), [5,10), [10,20), [20,infinity). The upper open-ended bound is null. Changing bins requires a new formula revision.

Partition the full requested elapsed window into nonoverlapping support cells. Classify each cell once: known valid calm speed is calm even without useful direction; qualified noncalm speed/direction is directional; explicitly instrument/quality-classified variable direction is variable; missing/invalid required support is missing. A weak *aggregate* resultant does not relabel every directional observation variable. Unknown direction is missing evidence, not observed variability.

Each public wind-rose product carries structured `wind_rose` metadata: category, speed min/max, duration and denominator seconds. All categories use the full elapsed requested interval as denominator, including missing time. Thus directional + calm + variable + missing durations sum to that interval. Display frequency is duration/denominator; distinguish this from metric-validity coverage. Never silently renormalize only noncalm bins to 100%. Category labels and speed/direction units remain visible.

For directional entries the existing angle fields carry the half-open sector edges, including explicit north wrap. Calm/variable/missing entries use null angles and null speed bounds. Semantic validation checks edge/bin membership, speed upper > lower where finite, duration <= denominator, common denominator equality to requested interval, total partition conservation and aggregate fraction equality. JSON Schema validates shape and presence, not those cross-field equations. No client should parse speed bins or categories out of label text.

## Almanac records and comparisons

Records are within available station history, never climatological normals. Initial ranking policy: only windows with >=95% qualified metric-duration coverage enter the standard record ranking; label extrema as observed extrema and expose coverage/history interval even for qualifying windows. Lower-coverage observations remain browseable but outside that ranking. This threshold is a selected versioned policy, not proof that a missed extreme could not occur. Unknown rain support cannot qualify a total through sample count alone.

Comparisons show each window's actual dates, duration, coverage and missing days. Inadequate windows are explicitly qualified rather than interpolated or rescaled. Sun/moon context uses the pinned local library/private location, published as derived context without coordinates; library/ephemeris pins remain required. No new external forecast or gardening metrics are introduced. Late corrections recompute affected daily/monthly/yearly products and rankings without waiting for AI.

## Acceptance and limits

WX-13/41/42 test 359/1-degree wrap, opposing directions, unequal speed/duration, calm and missing direction, exact bin boundaries, overlapping support, repeated packets, zero support, DST days, rain conservation, integer lightning and delayed revisions. Check speed and direction coverage independently and prove wind-rose categories conserve the whole window. UI/text summaries, exports, measured filters and synthesis must consume the same effective evidence revision. The API fixtures prove structured-bin shape only; no parser, aggregation, query, chart or station calibration is qualified here.
