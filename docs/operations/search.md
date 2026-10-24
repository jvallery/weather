# Search

`POST /api/v1/search` ranks published videos by exact cosine over `embed.main` vectors, English
full text and typed whole-video measurements (#25, P10). Code: `app/weather/api/search.py`
(query path), `app/weather/search/` (Cortex client and document builder). Schema:
`app/migrations/versions/0004_search.py`. Contract: [api-contract.md](../contracts/api-contract.md#search-implemented-25).

## How it works

- **Documents.** `weather.search_document` holds immutable revisions of public text: one
  `video` document per video (embedded) and optional hour `window` documents (full text only).
  Text is six lines, `Camera / Interval / Segment / Analysis mode / Observed conditions /
  Evidence quality`, built only from the effective public description (`VideoText`); there is
  no slot for favorites, notes, operator identity or originals metadata. `text_hash` is checked
  against the text, and `lexical` is a generated `to_tsvector('pg_catalog.english', text)`.
- **Builder interface.** Producers call `weather.search.documents.replace(connection, video,
  profile_id=…, now=…)` inside the transaction that changes the public description
  (publication #18, descriptions #23, editor #44), and `withdraw(connection, video_id)` when a
  video is withdrawn. A newer revision withdraws every current document of the video at commit,
  so old text stops matching before any reindex. Same revisions and text are a no-op (outbox
  replay); older revisions or different text under the same revisions raise `StaleRevision`.
  Withdrawal is final: trigger `search_document_withdrawal_final` rejects any update of a
  withdrawn row, and `weather_worker` can update only `visibility`, `withdrawn_at` and
  `index_status`. A future migration that backfills or rewrites existing rows must run
  `ALTER TABLE weather.search_document DISABLE TRIGGER search_document_withdrawal_final` and
  `ENABLE TRIGGER` again inside its own transaction, and must never make withdrawn text public.
- **Embedding jobs.** `replace` enqueues `search.embed` (idempotent per document, profile and
  UTC day). The handler reads the text, releases its connection, calls Cortex, then stores the
  vector only if the document is still public with the same hash (compare-and-set under a row
  lock). A late job therefore cannot resurrect withdrawn text. `search_embedding` is keyed
  `(document_id, profile_id)`; a foreign key binds it to the document's `text_hash`, and a
  CHECK requires unit norm. The handler is registered by the worker that holds Cortex egress
  (`weather.search.documents.register`, wired with #23); until then the scheduler leaves these
  jobs queued.
- **Profiles.** `weather.embedding_profile` lists profiles; `embed-main-2560-v1` is seeded
  (2,560 dimensions, query prefix `Instruct: Given a web search query, retrieve relevant
  passages that answer the query\nQuery:`). The API ranks only `WEATHER_SEARCH_PROFILE`
  (default `embed-main-2560-v1`); vectors of any other profile never rank.
- **API reads** `public_api.search_documents` and `public_api.search_vectors` (security
  barriers: public, past `public_not_before` and `video_end + 630 s`). Full-text matching
  goes through `public_api.search_lexical(...)`, a SECURITY DEFINER SQL function owned by the
  migrator (`search_path=pg_catalog`, the same public filter inline, EXECUTE for
  `weather_api` only): `@@` is not leakproof, so through the barrier view it could not use
  the partial GIN index `ix_search_document_lexical` (public rows only). `video_id` is a foreign key to
  #18's `weather.video` (RESTRICT; videos are never deleted, only withdrawn).

### One request

1. Validate (400): explicit UTC `start`/`end`, query ≤512 characters without control
   characters or lone surrogates, ≤8 predicates, `top_k` 1–20, body ≤16 KiB, no unknown
   fields. Predicates need a window of at most 31 days.
2. Admit: two searches per pod; a third waits ≤100 ms, and at most four wait. Otherwise 429
   with `Retry-After: 1`. Traefik adds per-client limits (below).
3. Embed nonempty text with no database connection held: one attempt, ≤1.5 s including the
   wait for the single per-pod Cortex slot. Failure, timeout or an invalid vector (wrong count
   or position, wrong dimension, non-finite, zero or non-unit norm) degrades to `lexical` with
   `degraded_reason: embedding_unavailable`. A search that spends its whole budget waiting
   behind another search's embedding degrades with `embedding_busy` instead (metric reason
   `busy`): Cortex is healthy, the pod's one slot was taken. Empty or whitespace-only text
   never calls Cortex.
4. One READ ONLY transaction, 1.5 s cumulative and ≤1 s per statement (`lock_timeout` 100 ms,
   no parallel workers). A timed-out connection is invalidated, never reused.
   - Predicates: for each candidate video, #14's on-read aggregate over the whole video
     interval (precipitation `sum`, wind gust `max`, temperature `min`/`max`/`mean`). Units
     convert exactly (mm/in, m/s/km/h/mph, degC/degF) and compare at 1e-6. A missing value or
     coverage below `min_coverage` (default 0.95) never matches, even `= 0`.
   - Modes: empty text → `numeric` (newest `source_start` first, then video ID). Text with a
     vector → `hybrid_exact`: ≤200 exact-cosine candidates (video documents) and ≤200 lexical
     candidates (`plainto_tsquery('pg_catalog.english', …)`, `ts_rank_cd(…, 1)`, ties by
     document ID). Text without lexical terms is semantic-only, or empty `lexical` with
     `lexical_no_terms` when there is no vector. Filters are never loosened.
   - Fusion: `1/(60 + semantic rank) + 1/(60 + lexical rank)`, best document per video, ties
     by video then document ID, then `top_k`.
   - Re-hydration re-reads the chosen documents; one withdrawn meanwhile drops out.
5. 503 for a SQL failure or the 5 s request deadline, never an empty success. Every response
   and error is `Cache-Control: no-store`. Query text is never logged or used as a label.

A hit carries the cited document's interval and `evidence_precision` (`segment` for a video
document, `interval` for a window), its seek (media offset and source instant at the start
of the cited interval, or null), snippet, camera, media revision, poster and the whole-video
`matched_measurements` (value, unit, statistic, interval, coverage, evidence count and hash).

## Enabling

Search wiring lives in the kustomize component `deploy/k8s/search`: the `weather-cortex` key
mounted read-only at `/var/run/weather-cortex` in `api` only, `WEATHER_CORTEX_BASE_URL`
(`https://cortex.vallery.net/v1`), NetworkPolicy `api-cortex-egress`, and IngressRoute
`weather-search` with Middlewares `weather-search-rate-limit` (30/min, burst 10) and
`weather-search-in-flight` (2), both keyed on `Cf-Connecting-Ip` (C7). It stays disabled until
the `deploy:` commit that pins an app digest containing this code uncomments `components: -
search` in `deploy/k8s/kustomization.yaml`, because older images reject the new setting.
The migrate hook applies `0004_search` with that pin. Without the key or URL the API still
serves `numeric` and `lexical` search.

## Profile change

1. Seed the new profile row (a new revision), add it to `weather.search.cortex.PROFILES`.
2. Run `enqueue_missing(connection, "<new-id>", now)` (500 per call) until no current video
   document lacks a vector for the new profile; a worker configured with that profile embeds
   them. Count the remainder as `weather_worker`:
   `SELECT count(*) FROM weather.search_document d WHERE kind='video' AND visibility='public'
   AND NOT EXISTS (SELECT 1 FROM weather.search_embedding e WHERE e.document_id=d.id AND
   e.profile_id='<new-id>')`.
3. Flip `WEATHER_SEARCH_PROFILE` in a deploy commit. Rolling back is flipping it back; the old
   vectors stay until a later cleanup revision.

## Signals

Port 9000 `/metrics`:

| Metric | Meaning |
|---|---|
| `weather_search_requests_total{mode,outcome}` | Responses by mode (`hybrid_exact`, `lexical`, `numeric`, `none`) and outcome (`ok`, `degraded`, `busy`, `unavailable`, `invalid_request`, `range_too_large`) |
| `weather_search_duration_seconds{mode}` | Latency of successful searches |
| `weather_search_embedding_failures_total{reason}` | Query embeddings that fell back: `busy` (waited out the budget behind another embedding in the pod), `timeout`, `error`, `invalid`, `unconfigured` |
| `weather_search_timeouts_total{stage}` | `sql` budget or the 5 s `request` deadline exhausted (503) |
| `weather_search_index_backlog` | Current video documents without a vector for the active profile (read every 60 s) |

`weather_api_request_duration_seconds{route="search"}` also covers 400/429/503.

## Checks

```sh
curl -s -X POST https://weather.vallery.net/api/v1/search -H 'content-type: application/json' \
  -d '{"start":"2026-07-01T00:00:00Z","end":"2026-08-01T00:00:00Z","query":""}' \
  | jq '{mode, count}'
kubectl --context vallery -n weather exec deploy/api -- python -c \
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:9000/metrics').read().decode())" \
  | grep -E '^weather_search_'
kubectl --context vallery -n weather get networkpolicy api-cortex-egress
kubectl --context vallery -n weather get ingressroute weather-search
```

## Failure modes

| Symptom | Likely cause | Action |
|---|---|---|
| Text searches report `lexical` + `embedding_busy` | Concurrent text searches in one pod share its single Cortex slot | Expected under bursts; persistent `busy` with low Cortex latency means more text-search load than one slot per pod serves |
| Text searches report `lexical` + `embedding_unavailable` | Cortex down, slow (>1.5 s), key missing or vectors invalid | Check `weather_search_embedding_failures_total` by reason; `kubectl get secret weather-cortex` (metadata only) and the component's egress policy. Numeric and lexical search keep working. |
| `weather_search_index_backlog` grows | No worker runs `search.embed`, or Cortex refuses document embeddings | Check queued `search.embed` jobs and the worker's logs (exception class only). Lexical matches still work meanwhile. |
| 429 on search | Pod admission full (two running, ≤100 ms wait) or Traefik per-client limits | Expected under bursts; persistent 429 at low traffic means slow SQL, see below |
| 503 on search | SQL budget, request deadline or database outage | `weather_search_timeouts_total`; narrow the window; check `weather-pg` health |
| 400 `range_too_large` | Measured filters over more than 31 days | Expected; split the window |

## Follow-ups

- Hydration joins `public_api.videos`, so a hit whose media is unavailable or withdrawn drops
  or loses its seek even before #18's withdrawal calls `withdraw`.
- #23 wires `register(registry, embedder)` into the worker that holds Cortex egress and calls
  `replace`; #44 calls `replace`/`withdraw` on editorial changes; #27 builds the UI.
- Live 429 bursts, seek checks and relevance on the real corpus are #50.
