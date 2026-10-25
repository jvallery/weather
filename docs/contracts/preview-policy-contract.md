# Preview eligibility, current display and retention

Requirements §6.11, as amended by P14 (eligibility), P8 (request-time authorization) and C17 (`weather-preview` serves previews). Implementation and tests: #39. The planning-era policy schema, shape fixtures and clock-uncertainty vectors are in the [archive](../archive/planning-2026-10/contracts/); the qualified clock adapter they assumed was removed by P14.

## Three separate decisions

1. **Earliest publication:** a frame is eligible when `capture_upper_bound + 600 s + skew_margin <= now`. `skew_margin` defaults to 30 s on NTP-synced nodes; the node clock-offset alert (monitoring-stack#1072) covers it, and no clock adapter or clock qualification exists. The database enforces `public_not_before >= capture_upper_bound + 630 s`. Equality is eligible. Unknown, future (`capture_upper_bound > now`), reversed or implausible bounds are never eligible. `public_not_before` expresses that earliest instant, not cache freshness.
2. **Current display:** age is `now - capture_lower_bound`, the older bound, so a frame is never labelled fresher than it may be. Through 1,200 s of age the pointer is fresh; above 1,200 through 1,800 s it is stale; above 1,800 s the latest-preview response is unavailable with a reason and no asset URLs. A new HTTP response, ETag, pointer generation or publication timestamp never resets source age.
3. **Logical retention:** `Image.expires_at = capture_lower_bound + 3,600 s` (migration `0007_previews`; the planning-era 86,400 s is withdrawn) is when the derivative stops being eligible for direct public access. It is **not** the 1,800 s current-display cutoff and **not** physical deletion. A still-eligible derivative may remain reachable after the latest pointer has become a placeholder. The producer deletes pointer rows at `expires_at` and files about 65 minutes after their generation, so `previews/` holds about an hour.

At exactly 1,200 s classify fresh, at exactly 1,800 s stale, and at `expires_at` deny access (half-open lifetime). Safety withdrawal may deny access sooner than natural expiry; a pointer refresh never extends retention.

## Serving

Every GET, HEAD and Range request for a pointer or its bytes is authorized at request time against the pointer row: embargo, expiry and withdrawal are evaluated before any header, validator or byte. Byte responses reuse the #20 media origin code and its [range policy](api-contract.md#range-policy). There are no authorization leases (P8). Pointer and byte responses are `Cache-Control: no-store`, including errors.

Only the `weather-preview` Deployment mounts staging `previews/` (read-only); the api has no NFS volume (C17). A pointer to a missing or hash-mismatched file reports unavailable, and its byte path returns the same 404 as an unknown ID. Public requests never reach a camera.

## Queue, handoff and loss

The producer reads sealed chunks only, never `tmp/`. One pending candidate per camera coalesces to the newest eligible sealed capture, with at most one generation per camera per minute. After an outage it does not replay missed minutes. A frame the collector flagged `duplicate_hash`, or whose sealed bytes equal the last published frame's (`source_sha256`, not public), is never published, so a frozen or cached camera's picture ages out instead of being relabelled with a new request time each minute. Each camera reports its own availability.

Persist the capture bounds, `public_not_before`, `first_published_at`, `expires_at` and generation, one `camera_preview` row per (camera, generation, variant) with variants `large` (≤1280 px) and `small` (≤480 px). Without a cache calibration the lower bound is the snapshot request's start; a finite upper bound needs no calibration (#75). An upper and lower bound more than 300 s apart is implausible and never eligible. The public `Image` carries `source_interval`, `exposure_upper_bound`, `first_published_at`, `expires_at`, `state` and `reason`; state is evaluated on each response while asset identity stays immutable. A client ages an already received preview locally and stops showing it as current at its cutoff even when polls fail. It may become more conservative during a connection failure, but never extends a server-authorized lifetime.

## Implementation (#39)

| Obligation | Where |
|---|---|
| Producer: sealed chunks only, newest eligible frame, ≤1 generation per camera per minute, no backlog | `app/weather/preview/producer.py` (runs in each `pipeline-<camera>`) |
| Sanitized derivatives: decoded, resized, re-encoded; no EXIF, XMP, GPS, ICC or comment (marker-checked) | `app/weather/preview/jpeg.py` |
| Database: CHECKs above, security-barrier `public_api.previews` on `clock_timestamp()`, API role reads only the view | `app/migrations/versions/0007_previews.py` |
| Request-time re-check with the database clock, pointer, bytes through #20's origin | `app/weather/preview/server.py` (`weather-preview`) |
| Client local aging, fresh/stale/unavailable tiles | `web/app/lib/preview.ts`, `web/app/components/camera-previews.tsx` |
| Fake-clock, sealed-only, outage, metadata, missing-file and stall tests | `app/tests/test_preview.py`, `app/tests/test_preview_integration.py`, `web/app/lib/preview.test.tsx`, `web/e2e/public.spec.ts` |

Operations: [media runbook, previews](../operations/media.md#previews).

## Test cases

Fake-clock tests cover each equality and one tick either side of the embargo, 1,200 s, 1,800 s and `expires_at`; unknown, future and reversed bounds; a cached pointer while time advances; failed polling; identical bytes served repeatedly without resetting age; a latest placeholder with a still-eligible derivative; and denied HEAD and Range requests, including ones carrying validators. No camera, model or network call is needed.
