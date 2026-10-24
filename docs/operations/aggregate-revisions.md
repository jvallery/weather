# Aggregate revisions

Migration `0005_aggregate_revisions` adds append-only computed revisions and mutable current heads.
It follows retained qualification history in 0004 and never changes an older migration snapshot.
The worker stores a typed public scalar document plus its exact rational value and private structured
wind/count/endpoint evidence. The public view reconstructs known scalar/coverage keys, canonical units
and source interval; private details and exact storage fields are not granted to API.

`aggregate_store.prepare` binds station, interval, public metric/statistic and policy into the stored
identity. It verifies the public projection against exact engine evidence and retains numerator,
denominator, the exact eligibility-evaluation instant and the public support-policy snapshot. Never
pass credentials or the runtime Settings object as that policy snapshot. A later verification
watermark can confirm unchanged input without inventing a new
computed value. Changed evidence or policy creates a distinct immutable record.

Only call `aggregate_store.persist` inside `JobContext.commit`'s fenced catalog transaction. It locks
the current head, rejects an older snapshot, verifies any reused identity against its complete stored
payload, appends the new revision and advances the head with job completion. Lost/expired ownership
rolls back both revision and pointer. Neither the worker nor API can update/delete a retained revision;
API can only select the public projection. No cleanup path deletes aggregate evidence.

Periodic persistence targets completed hours/weather days; in-progress windows are calculated on
read. Do not append a large active-day revision for every rapid packet. Relevant late arrivals or
quality corrections still create retained revisions of affected closed windows. Unrelated newer
watermarks cannot create historical revision churn when the complete selected inputs are unchanged.

This is source preparation. Five guarded PostgreSQL proofs cover retained late revisions, duplicate
work, stale snapshots, reused-identity conflicts, expired-fence rollback and public-role isolation;
they have only been collected, not executed on the Mac. The real cluster qualification must pass
before the revision handler is enabled. The telemetry.changed/conflict handler, full-window reader
and API paging integration remain part of WX-13 commissioning.
