# Weather events

Events (#41) are searchable, bookmarkable annotations over the archive: measured events derived
from canonical telemetry, validated visual/synthesis events supplied by #23, and manual
annotations written through the operator hook that #44 exposes. They are not a second telemetry
store and add no video render, notification, webhook or retention. Contract:
[weather-event-contract.md](../contracts/weather-event-contract.md). Code: `app/weather/events/`
(rules, write path, scheduler job) and `app/weather/api/events.py` (feed).

## Data model

Migration `0005_events`:

| Relation | Purpose |
|---|---|
| `weather.weather_event` | Identity only: opaque public `id`, private `logical_key`, `rule_family`, current `revision` pointer |
| `weather.weather_event_revision` | Immutable content per revision: visibility, origin/basis, camera scope (null = station-wide), half-open source interval (CHECK `starts_at < ends_at`), precision, rule revision, author kind (operator author and reason stay private), measurement, evidence IDs and hash. A trigger stamps the writing transaction id and time and rejects UPDATE/DELETE |
| `weather.weather_event_day` | The America/Denver days each revision intersects (from the pinned time model) |
| `public_api.event_revisions`, `public_api.event_revision_markers`, `public_api.event_days` | Security-barrier views for `weather_api`: public revisions' content, every revision's position, public day links |

`weather_worker` reads only `id, device_id, source_ts, values, quality` of `evt_precip`,
`evt_strike` and `obs_st`, plus a device's `station_id`; it appends events, revisions and day
links and may move an event's `revision` pointer. `weather_editor` (#44) has the same append
rights for the operator hooks below, and nobody can delete.

## Rules (each pinned by a rule revision)

- `precipitation_onset/1`: one event per valid, whole-second `evt_precip` row, interval
  `[t, t + 1 s)`. Invalid or sub-second timing produces no public event.
- `daily_gust_max/1`: after a local day closes plus 15 minutes, the largest valid `wind_gust > 0`
  among `obs_st` reports ending in `(day start, day end]`; ties go to the earlier report, then the
  lower observation id. Interval is the winning report's minute; coverage is the union of valid
  report minutes over the day's true length (23, 24 or 25 hours). One key per station and day.
- Lightning: `lightning_strike/1` (one per `evt_strike`, the selected authority) or
  `lightning_interval/1` (one per `obs_st` report with a positive integer count, over its
  `report_interval`, one minute when unknown). The authority
  is `LIGHTNING_AUTHORITY` in `rules.py`; switching it is a code change, and the next runs
  withdraw the other family's events in their window. Counts are integers.

## The job

The scheduler tick enqueues one `events` job per 5-minute slot (`events:<slot>`; due at the slot
end, deadline 15 minutes later), so repeated ticks are no-ops. Each job reconciles the 48 hours
before its slot end and every day that closed in it, inside its fenced job transaction:

1. Read candidate evidence in bulk and compare with each key's current revision.
2. For each key that differs, take the key's transaction advisory lock and row lock, re-read the
   evidence, then write a new revision or nothing. Evidence that changed while the job prepared
   is therefore re-read, and invalid evidence withdraws the public claim in the same transaction.
   At most 1,000 keys are settled per job (the write transaction holds the job row, so its
   lease cannot be extended meanwhile); the rest show as `deferred` in the job result and finish
   in the next slots.
3. Each revision writes its day links and one `weather_event.changed` outbox row. The scheduler
   routes that topic (kind `weather_event_changed`, a no-op until search #25 consumes it), so it
   is never quarantined.

A retry with the same evidence writes nothing. An operator override survives reruns on unchanged
evidence; changed evidence withdraws it with reason `override_stale` and keeps the human revision
until an operator settles it.

Late input older than 48 hours is not revisited automatically. To backfill, enqueue jobs with an
older `end` as `weather_worker` (one per 48 hours, oldest first), for example:

```sql
INSERT INTO weather.job(kind,idempotency_key,payload,not_before,deadline)
VALUES ('events','events-backfill:2026-10-20',
        '{"end":"2026-10-20T06:00:00+00:00"}',now(),now()+interval '30 minutes');
```

## Feed

`GET /api/v1/events?day=&camera=&origin=&order=&limit=&cursor=` (default 50, max 100) and
`GET /api/v1/events/{id}`. A camera filter returns that camera's events plus station-wide ones.
Pages order by (source start, id) over the first page's exact PostgreSQL snapshot
(`pg_current_snapshot`, carried in the signed cursor): each event appears once, with the newest
revision that snapshot saw, and only if that revision is public. Later inserts, moves and
withdrawals show in a new feed; withdrawal is immediate for every new read. Responses are
`no-store`. A missing, private or withdrawn event is the same 404. After a PITR restore, discard
outstanding cursors (they expire within 24 hours anyway).

Video links come from UTC overlap with each camera's playable segments in `public_api.videos`
(published or partial, not withdrawn; one bounded read per page, #40), with seeks through the
segment's uniform time map (frame k owns `[a + k(b − a)/N, a + (k + 1)(b − a)/N)`, 60 fps). A camera
without overlapping footage gets an explicit `unavailable` link. If that catalog read fails, every
link on the page degrades to `unavailable` and `weather_api_db_errors_total{operation="event_videos"}`
counts it; the feed itself still serves. Video pages seek to `?t=<UTC>` through the sidecar map.

## Operator hooks (#44)

`weather.events.store.override(connection, event_id, author=, reason=, visibility=, label=)`
changes only visibility or label; `write_manual(...)` writes a standalone manual annotation.
Both require an authenticated operator and a reason, are attributed as human, and never change a
station measurement. The editor (#44) calls them as `weather_editor` with the allowlisted
identity as author, in the same transaction as its own revision, audit and outbox rows; see the
[editor runbook](editor.md).

## Checks

```sh
kubectl --context vallery -n weather exec deploy/scheduler -- python -c \
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:9000/metrics').read().decode())" \
  | grep -E '^weather_(events_revisions|jobs_failed|outbox_quarantined)'
curl -s 'https://weather.vallery.net/api/v1/events?limit=5&order=desc' | jq '.items[] | {kind, origin, source_interval}'
```

```sql
-- As weather_worker: recent job outcomes, and events by family and visibility.
SELECT status, result, last_error FROM weather.job WHERE kind='events' ORDER BY not_before DESC LIMIT 5;
SELECT e.rule_family, r.visibility, count(*) FROM weather.weather_event e
JOIN weather.weather_event_revision r ON r.event_id=e.id AND r.revision=e.revision GROUP BY 1,2;
```

## Failure signals

| Signal | Meaning / action |
|---|---|
| `weather_jobs_failed_total{reason="handler"}` rising with `events` jobs failed | Check the sanitized class in the scheduler log; the next slot retries the same window |
| `weather_events_revisions_total{action="withdrawn"}` jumps | Evidence invalidated, an authority switch, or stale overrides; inspect the revisions' `withdrawal_reason` |
| `weather_outbox_quarantined_total` with `weather_event.changed` | A scheduler without the route ran; deploy the current scheduler, then re-queue (see database.md) |
| `weather_api_db_errors_total{operation="events"}` | Feed read failed or timed out (1.5 s); check database health |
| `weather_api_db_errors_total{operation="event_videos"}` | The page's catalog read failed; links show `unavailable` until it recovers |
