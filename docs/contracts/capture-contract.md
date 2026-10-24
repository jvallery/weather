# Capture and single-USTAR manifest v2

The accepted [simplification P2](../decisions/2026-10-24-simplification.md) and
[WX-10 (#11)](https://github.com/jvallery/weather/issues/11) supersede the former
writer-epoch/fencing, three-file chunk directory, binding-file and promotion
working-set design. The collector has no coordinator, database, Kubernetes or
internet access. Camera commissioning and actual NFS behavior belong to WX-03.

The code-first private record union lives in `app/weather/capture/records.py`.
`scripts/generate_capture_contract.py` renders [capture.schema.json](capture.schema.json).
The shape fixtures and strict byte-reader tests are separate: JSON shape does not
prove native JPEG decode or filesystem durability.

## Storage representation

One chunk is a plain USTAR containing zero or more regular
`frames/<attempt_id>.jpg` members and one **final** `manifest.jsonl`. Original camera
bytes are retained exactly. Attempt IDs are ASCII, at most 89 characters; complete
member paths therefore fit USTAR's 100-byte name field without prefix splitting,
PAX or GNU extensions. Readers reject links, devices, unknown members, traversal,
duplicate names, invalid headers/checksums, nonzero padding, incomplete termination,
unbound members and length/hash mismatches. They never extract generic TAR input.

A collector exclusively creates
`tmp/<camera>/<first-slot-ns>_<window-end-ns>__<pod-uid>-<incarnation>-<seq>.tar`
with `O_EXCL`. Each process has a fresh random incarnation. The final name is
`sealed/<camera>/<UTC-hour>/<pod-uid>-<incarnation>-<seq>.tar`. Default chunk duration
is 60 seconds, maximum 300; captured slots never cross a UTC hour. Neither two
processes nor restarted processes share a target name. No collector deletes a tmp
file or edits/deletes a sealed file. Cleanup belongs to WX-18.

Payload progress is flushed/fsynced at least every five seconds of active writing.
The final manifest and TAR termination are written, the file is fsynced and closed,
then renamed atomically and the source/destination directories are fsynced. Readers
ignore tmp files. NFS read/write errors before rename abandon the complete uncertain
file in tmp, record `persist_failed` loss accounting and never publish it.
A directory-fsync error after a successful rename is **commit uncertainty**: the
complete sealed file may exist. The collector leaves it untouched and emits
`seal_uncertain`; the scheduler reconciles it instead of asserting either loss or
success from the collector's return code.

## JSONL records and time

JSON is UTF-8, one object per LF-terminated line. Duplicate keys, non-finite numbers,
blank lines and partial final lines are invalid. The manifest is bounded to 1 MiB.
Only explicitly modeled fields are accepted; endpoints, credentials, response bodies,
station identities and exact site coordinates have no fields here.

- `chunk_header`: schema version 2, camera, pod UID, incarnation, sequence, safe
  operational settings and configuration hash. Endpoint and credential values are
  excluded from the hash/settings rather than published as reversible identifiers.
- `capture_attempt`: unique attempt/slot identity, scheduled UTC, clock epoch,
  captured/skipped/request-failed status and a bounded failure class. Captured rows
  bind the exact member name, byte count, SHA256 and decoded geometry. Identical
  consecutive hashes set `duplicate_hash` but both frames remain valid inputs.
- `skipped_range`: a compact, reconstructable consecutive monotonic slot range and
  count, for overdue slots. It prevents a long outage from creating a catch-up loop
  or unbounded metadata allocation. It does not issue camera requests.
- `loss_range`: definite in-process persistence failure, first/last scheduled slot
  and count. When the mount is unavailable, individual details cannot be retained;
  this constant-memory summary explicitly records that loss of detail.
- `unsealed_range`: an uncertain tmp candidate, commit-uncertain seal, or bounded
  inventory overflow. It is never a captured-success or definite-loss assertion.

The monotonic timer advances absolute deadlines and never launches overlapping
attempts. All physical HTTP exchanges, including Digest negotiation, are also
limited to the commissioned cadence. Connect/read defaults are 1/3 seconds; a
separate five-second monotonic deadline covers negotiation, retry and the entire
body. Redirects are rejected. Transport backoff is 1/2/4/8/16/30 seconds;
authentication backoff is at least 60 seconds.

Each request records UTC start/response-end, monotonic nanoseconds as decimal
strings, clock epoch and a request-midpoint source estimate. Without a mounted
cache calibration, uncertainty and exposure bounds are null and freshness is
`unknown`. With calibration, bounds are `[request-start-cache-allowance, response-end]`;
uncertainty is the maximum distance from the request midpoint to either bound.
A wall-clock step greater than one second changes the clock epoch and invalidates
finite bounds for that request. HTTP Date and equal hashes never establish exposure
certainty or staleness. A finite bound alone does not qualify public eligibility;
request-time embargo and independent clock-health checks remain mandatory.

Slot IDs combine a fresh clock epoch and increasing sequence; a backward clock step
or process restart cannot reuse one. The scheduler deduplicates measured capture
slots using camera plus scheduled UTC/cadence and selects at most one accepted
attempt, retaining distinct attempt IDs and clock uncertainty. Timestamp coincidence
is not a reason for the collector to erase evidence.

## Hard kills and reconciliation

A hard kill can lose an in-memory attempt ledger before the final manifest exists.
On startup a collector may inventory **names only** in its camera's tmp directory;
it never opens, modifies or removes another incarnation's file. A foreign writer
may still seal successfully. Filename windows therefore produce
`unsealed_uncertain` candidates, never `persist_failed` assertions. The scheduler
must reconcile candidates against sealed chunk identities and actual accepted slots
before assigning coverage loss. Repeated candidate IDs are idempotent observations.
At most 128 windows are retained in memory; any overflow count explicitly requires
further scheduler inventory. A filename never proves a successful capture.

Normal in-process EIO/ESTALE/ETIMEDOUT/ENOSPC has separate tests from hard-kill
uncertainty. Recovery never silently repairs or publishes an uncertain tmp tail.
