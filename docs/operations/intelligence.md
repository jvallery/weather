# Intelligence: AI weather descriptions

The `intel` worker (#23, P9) writes a labelled "AI-generated · sampled-frame analysis" description for every published video from its own web-rendition frames, the segment keogram when one exists (#37), and #14's on-read aggregates. Code: `app/weather/ai/`. Schema: `app/migrations/versions/0011_analysis.py`. Contract: [narrative contract](../contracts/weather-narrative-contract.md). Manifests: `deploy/k8s/intel/` (disabled; #50 enables them).

## How it works

1. **Queueing.** The scheduler's `media.changed` hook (`media.published` outbox, after the embargo) upserts the video's head row in `weather.analysis` as `pending` and queues `analysis.describe`. A republish with the same web-rendition SHA-256 queues nothing: analysis is pinned to the media, model and prompt it was made with, and nothing re-analyses the archive automatically.
2. **Stage A, visual.** `intel` downloads the web rendition from `weather-media` with the read-only reader key, verifies its content address, and decodes up to 8 frames per UTC-hour window in one FFmpeg pass (windows: [analysis-window vectors](../contracts/analysis-window-vectors.json); a window with no frame is unreviewed and borrows nothing). Each window is one image request: the frames (JPEG, ≤768 px wide), the keogram columns for that hour when a keogram is published, camera orientation and caveats. It never carries a measurement. The images are capped client-side at 1 MiB each and 4 MiB per request.
3. **Stage B, measured.** The packet holds whole-segment aggregates (temperature min/max/mean, humidity, mean wind, peak gust, rain total, station pressure, lightning count) with packet-local IDs and application-rendered values.
4. **Stage C, synthesis.** One text request with the validated observations and the packet. The reply must cite supplied IDs, and every number must be a supplied rendering of a cited metric.
5. **Validation** rejects truncation, malformed JSON, schema violations, unknown or stale IDs, invented, converted or irrelevant numbers, markup and URLs, instruction-like text, and any mention of people, vehicles, deliveries or household activity. A rejected reply gets one repair request that names only the rejection code.
6. **Publication** is one fenced transaction with compare-and-set on the head's media SHA-256 and base revision, so a late result never replaces a newer one. It appends an immutable `weather.analysis_revision`, emits visual events through #41 (clearly visible rain, snow, lightning or fog only, with fixed labels), and replaces the search documents (#25), which queues `search.embed`.

States (`weather.analysis.state`, served as `analysis.state` on `/api/v1/timelapses`):

| State | Meaning |
|---|---|
| `pending` | Not analysed yet, or Cortex was down, busy or refused the key. Retried at the next 02:00 America/Denver. |
| `complete` | Synthesis validated. Coverage may still be partial if some hours failed visual validation. |
| `degraded` | Synthesis did not validate after its budget: the deterministic measured summary plus validated, clearly visible observations. |
| `failed` | The rendition could not be read or validated, neither imagery nor measurements were usable, or it was still pending after 7 days. |
| `unavailable` | API only: the video has no playable media. |

When late telemetry changes a recent revision's measurements (checked every 15 min for 3 days after the video ends), the head is marked `stale` and one `analysis.synthesize` job per video, base revision and local day reruns Stage C against the stored Stage A observations. A video gets at most 3 such refreshes.

## Budgets and privacy

- **One Cortex call in flight** across every intel process: the session advisory lock `hashtextextended('weather.cortex', 23)`, which also covers `search.embed`. After a client timeout the lock is held 30 s more, because the server may still be working.
- **Per window**: the first try, two transport retries (5 s, then 20 s, or a `Retry-After` up to 60 s) and one repair, so at most 4 calls. Auth (401/403) and media (400/413/415/422) errors stop at once. **Per run**: 80 calls. Temperature 0.1, 120 s timeout, route `llm.primary.longctx.mm` only. The config refuses any base URL other than `https://cortex.vallery.net/v1`.
- **Raw replies** go to `weather.ai_raw_response` with an outcome code. A CHECK caps `expires_at` at 24 h, and the intel tick deletes expired rows every minute. Only `weather_worker` can read the table; the API, sidecars and logs never carry a reply body. Logs carry exception class names only.

## Metrics

`/metrics` on port 9000: `weather_ai_queue_depth`, `weather_ai_oldest_pending_seconds`, `weather_ai_outcomes_total{outcome}`, `weather_ai_calls_total{stage,result}`, `weather_ai_call_seconds{stage}`, `weather_ai_rejections_total{stage,code}`, `weather_ai_raw_purged_last` and `weather_intel_database_ready`. Alert rules belong to monitoring-stack (#50).

## Enable (#50)

1. Pin an app digest that carries `weather.ai` and migration `0011_analysis` (the migrate hook applies it).
2. Run the [live smoke](#live-smoke) against that digest.
3. Add `- intel` to the root `deploy/k8s/kustomization.yaml` resources in the same `deploy:` commit. It needs Secrets `weather-cortex`, `weather-media-s3-reader`, `weather-pg-worker` and `weather-pg-ca`.
4. Verify that Argo `weather` is Synced and Healthy, the `intel` pod is ready, and `weather_ai_queue_depth` drains. The next published video should turn `complete` or `degraded` on its page.

## Live smoke

`deploy/k8s/intel/smoke-job.yaml` makes one image call with a generated (non-camera) image through the scoped key. It has no database or Garage access, publishes nothing and prints metadata only.

```sh
sed -e 's/REPLACE_WITH_DIGEST_UNDER_TEST/<digest>/' -e "s/YYYY-MM-DD/$(date -u -v+1d +%F)/" \
  deploy/k8s/intel/smoke-job.yaml | kubectl --context vallery apply -f -
kubectl --context vallery -n weather wait --for=condition=complete job/weather-intel-smoke --timeout=200s
kubectl --context vallery -n weather logs job/weather-intel-smoke   # {"result": "http_200", ...}
kubectl --context vallery -n weather delete job,networkpolicy weather-intel-smoke
```

A non-200 result prints only its class (`unavailable`, `busy`, `auth`, `media`).

## Operations

- **Pending for a long time.** Check `reason` in `weather.analysis`: `cortex_unavailable` and `cortex_busy` point at Cortex (cortex-ai-compute), `cortex_auth` at the key. To retry now instead of at 02:00, run `UPDATE weather.analysis SET next_attempt_at=now() WHERE video_id='<id>' AND state='pending'`. The next tick queues it.
- **Why was a reply rejected?** For 24 h, `SELECT stage,window_start,call,outcome,finish_reason FROM weather.ai_raw_response WHERE video_id='<id>' ORDER BY created_at` (as the worker or migrator role). Read `body` only when debugging, and never copy it into issues or logs.
- **Re-analysing on purpose** (new prompt or model) is a decision, not a routine: a new prompt revision applies to new publications only. Backfills are bounded runs (#50).
- **Withdrawal.** `weather media withdraw` withdraws the video. The `media.withdrawn` hook then withdraws its search documents and visual events, and the public view hides the analysis.
- **Key renewal.** The scoped `weather-cortex` identity expires 2027-01-22; renew it before 2027-01-08 through cortex-ai-compute and agents KSOPS, then rerun the live smoke.
