# Logging (#147)

Every Weather process writes one JSON object per line to stderr (`weather.logs.configure`):
`ts` (UTC), `level`, `component` (`collector`, `pipeline`, `scheduler`, `intel`, `preview`,
`receiver`), `logger`, `msg`, and for state changes an `event` with stable scalar fields. Values
that look like a path, URL or address are refused at the call site, and an exception logs only
its type (`error`). Steady state stays below about one line a minute per pod.

## Events

| Component | `event` | Level | Fields |
|---|---|---|---|
| collector | `staging_stop` / `staging_resume` | warning / info | `camera`, `ratio` (the 90% stop and 75% resume) |
| collector | `capture_loss` | warning | `camera`, `window_seconds`, one count per lost result (`overdue`, `timeout`, `staging_full`, …); only minutes that lost slots |
| pipeline | `segment_finished` | info | `camera`, `segment_id`, `cycle_date`, `segment`, `state`, `reason`, `slot_coverage`, `gap_frames`, `frames`, `late_seconds` |
| pipeline | `cleanup_pass` | info | `camera`, `apply`, and the pass report counts (deleted, kept by condition, bytes) |
| scheduler | `job_failed` | error | `kind`, `attempt`, `error` |

Plain lines (`msg` only) remain for errors that predate this format, for example
`Pipeline publish failed (camera, Type)`.

## Loki queries

The 2026-10-26 staging incident, reconstructed from logs alone:

```logql
{namespace="weather"} | json | event=~"staging_stop|staging_resume"
{namespace="weather"} | json | event="capture_loss" | line_format "{{.camera}} overdue={{.overdue}} full={{.staging_full}}"
{namespace="weather"} | json | event="cleanup_pass" | apply="true"
{namespace="weather"} | json | event="segment_finished" | line_format "{{.camera}} {{.cycle_date}} {{.segment}} {{.state}} coverage={{.slot_coverage}}"
{namespace="weather"} | json | level="error"
```

Lost slots per camera over a day:
`sum by (camera) (sum_over_time({namespace="weather"} | json | event="capture_loss" | unwrap overdue [24h]))`.
