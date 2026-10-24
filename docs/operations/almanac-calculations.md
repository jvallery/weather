# Almanac calculation preparation — #42

This is bounded source preparation, based on the WX-13 typed computation
boundary. #42 remains blocked by native prerequisites #14 and #21; #10 is
closed. #42 is explicitly not a #48 launch gate. No almanac route, DB query,
revision publication, scheduler handler or UI is delivered by this source.

`app/weather/products/almanac.py` consumes WX-13 `Computation` objects and
reuses public `Aggregate`, `Interval` and `WindRoseBin` contracts. It does not
parse telemetry, qualify holds, reconcile rain, deduplicate packets or run a
second wind recipe. The caller must select one consistent effective snapshot.
Ambiguous competing aggregate revisions for the same window are refused.

Prepared behavior:

- Records preserve every supplied day/month/year window. Only non-null
  minimum/maximum observations with at least 95% metric-duration coverage are
  ranked; ties use competition ranks. Thin observations remain browseable.
  The label says "observed extremes within station history since …", never
  normals. A partial first history day may start before the first observation.
  There is no device identity grouping: replacement at the same site does not
  reset history.
- Rain rows carry the original qualified observed accumulation and its reason.
  A half-covered three-mm day stays three mm; unknown support remains null.
- Comparisons preserve both source intervals and elapsed durations. The prepared
  adequacy policy is the versioned 95% threshold; a thin window suppresses the
  difference. Otherwise difference is second minus first, with no duration
  normalization or extrapolation. `missing_days` means dates touched by a
  qualified-support gap, including partially covered dates.
- Wind metadata projects the existing classifier's 83 structured bins and full
  elapsed denominator. Calm, variable and missing remain distinct. It does not
  classify samples again or infer categories from a mean resultant.
- Local periods use the shared pinned timezone and `weather_day`, including
  actual 23/25-hour days and unequal calendar-month durations.

Synthetic vectors: `docs/contracts/almanac-vectors.json`. Focused checks:

```sh
PYTHONPATH=app python -m pytest -q app/tests/test_almanac.py
ruff check app/weather/products/almanac.py app/tests/test_almanac.py
mypy app/weather/products/almanac.py
```

The focused suite denies socket connects and DNS resolution during computation.
It proves calculations and immutable old Python results only. It does not prove
DB retention of prior revisions, correction/outbox execution or query snapshots.
DB/integration, browser/media and live checks must run in the cluster.

Record ordering, eligibility and comparison arithmetic use WX-13
`Computation.exact` values and coverage; public float projection is output only.
The false-tie regression preserves ordering when two public floats are equal.
The almanac API still needs its bounded signed cursor, comparison-bound 400s,
common interval/history/timezone/formula/library/evidence revision envelope and
per-product public Aggregate projection. Astronomy must extend the shared
pinned Astral wrapper without exposing private site configuration. The outbox,
UI charts/text alternatives, accessibility and live history checks remain #42
acceptance work. Do not mark #42 ready, closed or deployed from these fixtures.
