# Measured aggregate calculations

This is the calculation component of WX-13 (#14), currently source preparation.
It is not a deployment or station qualification. The database projection,
revision/outbox handler, current/history/export HTTP routes, cursor custody,
OpenAPI/types drift check and live acceptance remain part of that issue.

`app/weather/products/aggregate.py` consumes already qualified, reconciled
canonical-unit support. A packet receipt timestamp never supplies an observation
timestamp or an accumulation interval. The receiver/parser retains raw evidence;
the API adapter must choose the commissioned per-field support policy.

## Support and missing time

All windows are half-open UTC intervals, measured in integer microseconds.
Coverage is a union divided by the full elapsed window, including missing time.
Calendar-day callers supply the actual database/Python calendar bounds; a day is
not assumed to be 24 hours. The source tests use the 23/24/25-hour golden dates.

`forward_holds` requires an explicit per-metric cap. Every next observation,
including an invalid one, stops the preceding hold. Equal eligible overlapping
values share support once; disputed overlapping values are excluded for the
overlap, and a known invalid interval masks that interval. A missing value is
`None` with a reason, never a measured zero. Scalars use exact rational arithmetic
internally; rounding belongs to display conversion.

Rain amounts with qualified support split in exact proportion to overlap and
carry the label `uniform intensity over support`. Unknown support is excluded.
Distinct overlapping accumulation reports must be reconciled before summation;
the library rejects them instead of counting both. A longer packet report
interval never expands the preceding-minute rain support by itself.

Lightning requires one explicitly selected interval-count family. Event packets
are annotations, not an alternative count authority. Only fully contained
integer counts enter `observed_subtotal`; boundary-straddling counts remain in
`boundary_uncertain_count`. When no contained support exists, the public value
is unavailable even though the observed subtotal is zero.

## Wind, solar and pressure

Wind direction is a speed-and-duration-weighted meteorological vector.
Defaults are calm below 0.5 m/s and variable aggregate direction below resultant
0.1; these policy values enter computation provenance. Direction coverage counts
the usable noncalm speed/direction intersection, separately from speed coverage.
A weak aggregate vector does not relabel individual rose cells as variable.

The rose partitions the whole window into 16 half-open direction sectors and
five speed bins, plus calm, explicit variable and missing categories. North
wraps from 348.75 to 11.25 degrees. Calm does not require a direction;
unknown direction is missing, not observed variability. All bin frequencies use
the full elapsed denominator. Gust is the eligible reported gust maximum.

Solar energy integrates supported W/m2 into Wh/m2 without extending missing
time. Pressure stays station pressure. Trend requires explicitly configured
endpoint width and minimum support, computes duration-weighted endpoint means,
and uses their actual supported temporal centroids. It neither applies a
sea-level correction nor substitutes nominal endpoint spacing through gaps.

## Revision and verification

Computation identity binds the metric, statistic, units, formula parameters and
effective support policy, separately from the coverage algorithm and input
evidence revision. Aggregate IDs bind those identities, exact source window and
qualified inputs; receive order is irrelevant. The database handler must still
commit later evidence revisions and causal outbox invalidations atomically.
The pure identity test does not prove that database behavior.

Focused tests exercise the calculation portions of telemetry vectors T10-T15,
revision sensitivity, DST elapsed time, conflict/duplicate support, wind wrap
and opposition, sector/speed edges, category conservation, null versus zero,
solar gaps and supported pressure endpoints. Protocol commissioning, database
crash/cache/export saturation tests, and the T19 transactional invalidation
proof remain in-cluster acceptance work. Never treat synthetic fixtures as
evidence of physical station cadence, rain support or delivery.
