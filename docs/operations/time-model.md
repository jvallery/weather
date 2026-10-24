# Shared time model

`weather.timemodel` is a pure library, with no deployment or camera access. Capture,
rendering, keograms, hourly products and almanac code use the same frozen UTC
boundaries. The scheduler persists schedules; this library does not rewrite
previously admitted cycles.

## Private site and provenance

Load a mounted private JSON file with `load_site(path)` or `WEATHER_SITE_FILE`.
Without a selected file, `load_site()` reads `WEATHER_SITE_LATITUDE` and
`WEATHER_SITE_LONGITUDE`. Optional `WEATHER_SITE_` fields are `TIMEZONE`,
`DAWN_OFFSET_SECONDS`, `DUSK_OFFSET_SECONDS`, `ELEVATION_METERS` and `HORIZON_MODEL`.
The JSON names are their lowercase equivalents. Unknown/duplicate fields,
nonfinite coordinates and invalid offsets fail with `ScheduleError`. An invalid
file never falls back to environment values. Default offsets are zero and the
timezone is `America/Denver`.

Keep that projection private. Neither its values nor the complete site object
belongs in an API response, metric label or log. `SiteConfig` has a constant
redacted representation. Its private `config_hash` is SHA256 of compact, sorted
UTF-8 JSON of normalized site fields, horizon model, Astral version and timezone
package/IANA versions. Changing a field or model version changes that identity.

The civil model uses Astral 3.2, depression 6°, an unobstructed horizon and
observer elevation **zero**. Recorded site elevation remains in provenance but
never feeds Astral's height correction. Unknown horizon revisions cannot
calculate schedules. Sunrise and sunset are separate display events.

The timezone pin is `tzdata==2026.5`, IANA **2026e**. `site_timezone()` opens
that wheel's Denver TZif with `ZoneInfo.from_file`; installing the wheel alone
would still let system TZPATH win. The helper changes neither global TZPATH nor
another consumer's timezone lookup. `TZDATA_VERSION` and `IANA_VERSION` expose
the actual package constants. Sources: [tzdata](https://github.com/python/tzdata)
and [Astral](https://astral.readthedocs.io/en/latest/package.html).

## Calendar and solar identities

`solar_cycle(D, site)` calculates civil dawn and dusk on local date D and dawn
on **D + one local calendar day**, converts events to UTC, then shifts each
boundary once. Daytime is `[dawn, dusk)` and overnight is `[dusk, next_dawn)`.
The next cycle reuses that same shifted dawn. UTC elapsed durations determine
the frame split; dawn-to-dawn is not a fixed 86,400 seconds. `cycle_date` is the
local date of shifted dawn; local boundary strings retain their offset.

`weather_day(D)` instead means local midnight to next local midnight.
`hour_slots(D)` returns slots with UTC `start`/`end`, full offset-bearing
`local_label`, `fold` and a `weather_day` association. Cross-day associations are
rejected. `day_for(t)` and `slot_for(t)` use the source instant, never cycle date.
Repeated fall `01:00` slots keep different UTC identities, offsets and folds.

SQL helpers must compare every UTC bound, offset and fold with the Python helper
and [golden vectors](../contracts/time-model-vectors.json). Record the actual
PostgreSQL image and its timezone-data version separately. Matching the golden
23/24/25-hour dates proves those identities; it does not imply identical
PostgreSQL/Python timezone databases. Database FKs belong to the schema issue,
separately from this library's cross-day association guard.

## Frames and capture ownership

`allocate_frames(day_seconds, night_seconds)` accepts only integers or `Fraction`
values. It rounds daytime half up, assigning overnight the remainder of exactly
7,200 frames. Both must receive a frame. Only 60/1 fps is accepted.

`FrameMap.source_center(k)` returns exact UTC Unix seconds as a `Fraction`;
`source_offset(k)` is relative to segment start and `output_pts(k)` is `k/60`.
Keep those rationals for frame ownership and seeking; round only for display.
Terminal duration is `N*1000` ticks at timescale 60000, including the last sample.
Out-of-range indexes cannot borrow a neighbor's temporal context.

Pass `assign_capture(t, frozen_segments)` the admitted sequence covering t,
including following daytime when assigning next dawn. Dusk belongs to overnight;
next dawn belongs to next daytime. Overlap is an error and an uncovered instant
returns `outside_schedule`. Uncertainty bounds are inclusive: in strict mode an
upper bound reaching segment end returns `boundary_ambiguous` without a segment.
The caller records a gap.

## Failure and checks

Missing solar events, invalid observer input, reversed windows or zero-frame
results raise a sanitized `ScheduleError`. The scheduler must expose that error
and a visible gap. Never substitute midnight, sunrise, a neighbor or an earlier
configuration to continue silently. This library emits no metric/log itself;
the caller owns its error signal and retry policy.

`scripts/ci.sh unit` measures package line coverage with a 95% minimum.
`app/tests/test_timemodel.py` reads every positive/negative golden vector, sweeps
a synthetic year, mocks a non-86,400-second Astral clock, verifies packaged rules
without a global TZPATH change, and checks private representation/log redaction.
Tests use synthetic sites, with no real configuration, camera or media access.
