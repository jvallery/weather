# Intelligence: AI weather descriptions

The `intel` worker (#23, P9) writes a labelled "AI-generated · sampled-frame analysis" description for every published video from its own web-rendition frames, the segment keogram when one exists (#37), and #14's on-read aggregates. The model only classifies frames into enums; every public sentence is rendered by fixed server templates, and no model-written prose is ever published ([C18](../decisions/2026-10-24-takeover-replan.md)). Code: `app/weather/ai/`. Schema: `app/migrations/versions/0011_analysis.py`. Templates: [public description text](../contracts/weather-narrative-contract.md). Manifests: `deploy/k8s/intel/` (disabled; #50 enables them).

## How it works

1. **Queueing.** The scheduler's `media.changed` hook (`media.published` outbox, after the embargo) upserts the video's head row in `weather.analysis` as `pending` and queues `analysis.describe`. A republish with the same web-rendition SHA-256 queues nothing: analysis is pinned to the media, model, prompt and templates it was made with, and nothing re-analyses the archive automatically.
2. **Stage A, structured visual classification.** `intel` downloads the web rendition from `weather-media` with the read-only reader key, verifies its content address, and decodes up to 8 frames per UTC-hour window in one FFmpeg pass (windows: [analysis-window vectors](../contracts/analysis-window-vectors.json); a window with no frame is unreviewed and borrows nothing). Each window is one image request: the frames (JPEG, ≤768 px wide), the keogram columns for that hour when a keogram is published, camera orientation and caveats; never a measurement. The images are capped client-side at 1 MiB each and 4 MiB per request. The keogram is optional: one that is oversized (over 8 MiB), mis-addressed, unreadable or not a PNG/JPEG within 4 MP is dropped and the video is described from frames alone. Only an unusable web rendition fails a run.
3. **Validation** (`validate.py`) is strict schema validation of the [v2 schema](../../app/weather/ai/contracts/visual-analysis.schema.json): category, assessment, confidence, optional intensity (rain, snowfall, fog) and sky cover (cloud cover), limitation codes and supplied evidence IDs. Any free-text or unknown field rejects the reply; such text survives only in the private 24 h raw table. It also rejects unsupplied evidence IDs, a category twice in one window, a window without a precipitation and visibility assessment, and a misplaced intensity or sky cover. A rejected reply gets one repair request that names only the rejection code.
4. **Stage B, measured.** The packet holds whole-segment aggregates (temperature min/max/mean, humidity, mean wind, peak gust, rain total, station pressure, lightning count) with packet-local IDs and values formatted by code.
5. **Stage C, templates** (`render.py`, no model call). One visual sentence per category and phrase ("The sampled east-sky frames show broken clouds in all 3 reviewed hour windows."); `possible`, and `visible` at low confidence, always render hedged ("possibly show light rain"). Absence is worded only when provable: "No rain was visible in the sampled … frames" when every reviewed window assessed it `not_observed`, and a zero rain total only at full station coverage. Measured sentences print the station's values. Limitations are fixed sentences for unreviewed hours, a missing keogram, withheld station data, `not_assessable` categories and the model's limitation codes. Search text is the same template output.
6. **Publication** is one fenced transaction with compare-and-set on the head's media SHA-256 and base revision, so a late result never replaces a newer one. It re-reads the video `FOR SHARE`, and the withdrawal hook locks the head before reading event keys, so a withdrawal never misses events a concurrent publication wrote. A revision whose `author_kind` is not `model` is never replaced by model output for the same media and gets no evidence refresh; the worker cannot write `author_kind` (its INSERT grant excludes the column). The #44 editor keeps human text in its own `editorial_revision` rows (bound to the media and evidence it was written against), never in analysis revisions; while that text is active the API serves it as `analysis.author` `editor` under the editor's label, and AI text only as `ai`. Publication appends an immutable `weather.analysis_revision`, emits visual events through #41 (clearly visible rain, snow, lightning or fog only, with fixed labels), and re-projects the search documents (#25) through the editor's shared projection, which queues `search.embed`. An active human-edited description stays the public and searchable text across a rerun on unchanged evidence; changed evidence or new web bytes withdraw it until the operator rebases ([editor](editor.md)).

States (`weather.analysis.state`, served as `analysis.state` on `/api/v1/timelapses`):

| State | Meaning |
|---|---|
| `pending` | Not analysed yet, or Cortex was down, busy or refused the key, or the inference slot stayed busy or unreachable. Retried at the next 02:00 America/Denver; each nightly queueing moves the slot one night on, so a lost job is queued again. |
| `complete` | Every available hour window passed Stage A validation. |
| `degraded` | Some windows did not (`visual_partial`), none did (`visual_invalid`), or the call budget ran out (`call_budget`): the text covers the reviewed windows plus the measured summary. |
| `failed` | The rendition could not be read, neither imagery nor measurements yielded a sentence, or it was still pending after 7 days. |
| `unavailable` | API only: the video has no playable media. |

When late telemetry changes a recent revision's measurements (checked every 15 min for 3 days after the video ends), the head is marked `stale` and one `analysis.synthesize` job per video, base revision and local day re-renders the templates from the stored observations and the new packet. It makes no Cortex call, so it never waits on Cortex or the breaker. A video gets at most 3 such refreshes.

## Budgets and privacy

- **One Cortex call in flight** across every intel process: the session advisory lock `hashtextextended('weather.cortex', 23)`, which also covers `search.embed`. After a client timeout the lock is held 30 s more, because the server may still be working. Unless the unlock is confirmed, the slot's connection is invalidated rather than pooled (cancellation during the try-lock, grace or unlock, or a database error), which drops the session and its lock. A slot still busy after 10 min (`slot_busy`) or a database error taking it (`slot_unavailable`) defers the video; a failed release after a good reply keeps the reply.
- **Breaker.** Once a run ends in `cortex_unavailable`, `cortex_busy` or `cortex_auth`, the intel process makes no further chat call until the next 02:00 America/Denver, and queued describes defer straight away. Restarting the pod resets it.
- **Per window**: the first try, two transport retries (5 s, then 20 s, or a `Retry-After` up to 60 s) and one repair, so at most 4 calls. Auth (401/403) and media (400/413/415/422) errors stop at once. **Per queued describe**: 80 calls, metered in `weather.analysis.attempt_calls` so the job's scheduler retries share the budget; when it runs out the run publishes what its accepted windows support (`degraded`, `call_budget`). The nightly retry refills it only when it actually queues a job, and skips heads that still have a live describe job. Temperature 0.1, 120 s timeout, route `llm.primary.longctx.mm` only. The config refuses any base URL other than `https://cortex.vallery.net/v1`.
- **Raw replies** go to `weather.ai_raw_response` with an outcome code. A CHECK caps `expires_at` at 24 h, and both the intel tick and the scheduler tick delete expired rows every minute, so they are purged even while intel is disabled. Only `weather_worker` can read the table; the API, sidecars and logs never carry a reply body. The provider's model name is recorded and published only when it is identifier-shaped (`[A-Za-z0-9][A-Za-z0-9._:/+-]`, at most 128 characters; a CHECK enforces it); anything else is replaced by the route name. Logs carry exception class names only.

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

- **Pending for a long time.** Check `reason` in `weather.analysis`: `cortex_unavailable` and `cortex_busy` point at Cortex (cortex-ai-compute), `cortex_auth` at the key, `slot_busy` and `slot_unavailable` at the advisory lock or the database. A `degraded` head with `call_budget` kept failing until its calls ran out. After fixing Cortex, restart the `intel` pod to clear the breaker before forcing a retry. To retry now instead of at 02:00, run `UPDATE weather.analysis SET next_attempt_at=now() WHERE video_id='<id>' AND state='pending'`. The next tick queues it.
- **Why was a reply rejected?** For 24 h, `SELECT stage,window_start,call,outcome,finish_reason FROM weather.ai_raw_response WHERE video_id='<id>' ORDER BY created_at` (as the worker or migrator role). Read `body` only when debugging, and never copy it into issues or logs.
- **Re-analysing on purpose** (new prompt or model) is a decision, not a routine: a new prompt revision applies to new publications only. Backfills are bounded runs (#50).
- **Withdrawal.** `weather media withdraw` withdraws the video. The `media.withdrawn` hook then withdraws its search documents and visual events, and the public view hides the analysis.
- **Key renewal.** The scoped `weather-cortex` identity expires 2027-01-22; renew it before 2027-01-08 through cortex-ai-compute and agents KSOPS, then rerun the live smoke.
