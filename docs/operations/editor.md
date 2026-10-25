# Weather editor

The private editor (#44) lets Jason correct video descriptions and events, tag, favorite and
keep private notes at `https://weather.vallery.net/editor`, and read raw Tempest receipts.
Design: P11 in the [simplification record](../decisions/2026-10-24-simplification.md) and
the operator section of the [API contract](../contracts/api-contract.md#private-editor-implemented-44).
Code: `app/weather/editorial/` (API, CAS writes, projection, receipts) and
`web/app/routes/editor.tsx`.

## Request path and trust

```text
browser ──> Cloudflare tunnel ──> traefik-apps
   IngressRoute weather-editor (priority 200):
     /editor*            apps-strip-auth-headers → ak-forwardauth → web:80 (client-only shell)
     /api/v1/operator*   apps-strip-auth-headers → ak-forwardauth → api-operator:8001
```

- Authentik application `weather-editor` (agents#5366, `forward_single`, Jason-only binding,
  MFA, 24 h) issues the session. The app sets no cookies and has no login of its own.
- The api pod runs two listeners: the public one on 8000 (role `weather_api`, read-only) and
  the operator one on 8001 (role `weather_editor`). NetworkPolicy `api-ingress` admits 8001
  only from traefik pods in `traefik-apps`: not the web pods, monitoring or anything else.
  Service `api-operator` exposes 8001 alone; Service `api` never does.
- The public listener has no operator routes: `/api/v1/operator/*` there is a 404, and it
  never reads `X-authentik-*`.
- Every operator request needs exactly one `X-authentik-username` and
  `X-authentik-meta-app: weather-editor`, checked against `weather.editorial_operator` on
  each request (no cache): 401 without an identity, 403 when not allowlisted.
- Mutations (PATCH, POST) also need `Origin: https://weather.vallery.net` exactly, no
  cross-site `Sec-Fetch-Site`, and a JSON body of at most 64 KiB (403, 415, 413).
- Every operator response is `Cache-Control: no-store, private`. The web serves `/editor` as a
  static shell; all private data is fetched by the browser and kept only in memory, and
  mutations are never retried automatically.

## Writes

`PATCH /api/v1/operator/subjects/{video or event id}` with
`{"expected_revision": n, "reason": "...", "changes": {...}}`. One transaction, as
`weather_editor`:

1. CAS on `weather.editorial_subject` (the head). A second PATCH with the same
   `expected_revision` waits on the row, then gets 409 `revision_conflict` before writing
   anything.
2. Event changes call #41's `override` (label, visibility; withdrawal reason `operator`).
3. An immutable `editorial_revision` (the whole state: human description and its binding,
   public/private tags, favorite, note, event revision) and an immutable `editorial_audit`
   row (actor, time, reason, old/new revision, validated input, media SHA-256, evidence hash,
   event evidence IDs and revision). Both reject UPDATE and DELETE; a revert is a new edit.
4. One `editorial.changed` outbox row (payload `public` says whether public content changed),
   and, when the description, its binding or the public tags changed, the search projection:
   the new public text replaces the old documents, so the old text stops matching at commit.
   A favorite, note or private-tag edit moves nothing public: not the public revision
   (`Timelapse.editorial.revision`), not search, not the handler.

`POST /api/v1/operator/events` writes a manual event through #41's `write_manual`, with the
same revision, audit and outbox rows (action `manual_event`). Its interval must have ended at
least 600 s ago (the public embargo). Event tags are private only. A trigger lets only
`weather_editor` append an `operator` event revision, so the worker can never forge one.

### Human descriptions and evidence

A human description is bound to the video's web-rendition SHA-256 and the current analysis
evidence hash. It is public only while both still match (`weather.video_editorial_state`, the
one definition the API, search and AI share):

- An AI rerun on unchanged evidence keeps it active; the AI path re-projects through the same
  function and keeps the human text in search.
- New web bytes or changed measured evidence withdraw it from the API and search (the
  validated AI text, if any, becomes effective) but keep the revision. The editor shows the
  bound and current evidence; new description text or an event change then needs
  `"rebase": true` (409 `rebase_required` otherwise), audited as action `rebase`. Clearing
  (`null`) never needs one.
- `"description": null` clears the override; only eligible validated AI text remains.

Events follow #41: a rule rerun withdraws a stale operator override with `override_stale`;
`rebase` re-applies the last operator decision (or the requested change) to the current
evidence.

## Raw receipts

`GET /api/v1/operator/telemetry/receipts?start=&end=&family=&limit=&cursor=` returns at most
100 receipts over at most 24 h (400 `range_too_large` beyond), ordered by `(received_at, id)`,
with base64 bytes re-hashed against the stored SHA-256 before serving. Cursors are signed with
`CURSOR_HMAC_KEY`, bound to the filters and to the first page's `inserted_at` snapshot (database
clock − 120 s), so late spool drains never shift an export. The bytes carry device and hub
serials: never paste a response into an issue, log or chat.

## Checks

```sh
# Unauthenticated and forged requests end at Authentik (302), never at the API.
for p in /editor /api/v1/operator/subjects/x /api/v1/operator/telemetry/receipts; do
  curl -s -o /dev/null -w "$p %{http_code} %{redirect_url}\n" "https://weather.vallery.net$p"
  curl -s -o /dev/null -w "$p forged %{http_code}\n" -H 'X-authentik-username: jason' \
    -H 'X-authentik-meta-app: weather-editor' "https://weather.vallery.net$p"
done
# The operator port is unreachable from a web pod (expect a timeout).
kubectl --context vallery -n weather exec deploy/web -- node -e \
  "fetch('http://api-operator:8001/api/v1/operator/session',{signal:AbortSignal.timeout(3000)}).then(r=>console.log(r.status),e=>console.log('blocked',e.name))"
kubectl --context vallery -n weather get ingressroute weather-editor -o yaml | grep -A6 middlewares
```

```sql
-- As weather_editor: recent edits (no text is printed).
SELECT subject_id, action, actor, recorded_at, old_revision, new_revision
FROM weather.editorial_audit ORDER BY recorded_at DESC LIMIT 10;
```

## Operations

- **Allowlist.** One row, `jason` via `weather-editor`, seeded by `0013_editorial`. Change it
  only with a new migration; Authentik's own binding (agents) stays the first gate.
- **Editor credentials.** The operator listener reads `weather-pg-editor` at
  `/var/run/weather-db-editor`. Without them every operator route answers 503 and the public
  API is unaffected.
- **Outbox.** The scheduler routes `editorial.changed` (job kind `editorial.changed`) and
  re-projects the video's search documents, converging a race with an AI publication.
- **Sidecars** carry no description text (automated or human), so an edit never needs a
  sidecar revision.

## Failure signals

| Signal | Meaning / action |
|---|---|
| `weather_operator_denied_total{reason}` rising | `identity`: a request reached the listener without Authentik headers (check the IngressRoute chain); `allowlist`: a signed-in non-operator; `origin`/`content_type`/`body`: a refused cross-origin or malformed write |
| `weather_operator_requests_total{status="503"}` | Editor database role or credentials unavailable; check `weather-pg-editor` (metadata only) and `weather-pg` |
| 409 `rebase_required` in the editor | Media or evidence changed since the human revision; review old vs new and rebase |
| `weather_outbox_quarantined_total` with `editorial.changed` | A scheduler without the route ran; deploy the current scheduler |
