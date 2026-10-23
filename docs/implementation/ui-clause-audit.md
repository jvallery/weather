# Clause audit: linked plots, editorial controls and embed

Scope: requirements §§12.6–12.8, 38 sentence-level clauses. Every source sentence is retained verbatim in the [machine-readable ledger](ui-clause-audit.json), bound to the requirements hash. Compound sentences retain all obligations in their acceptance case. This is a partial design audit, not whole-document coverage or runtime acceptance.

The audit found an editorial write/public-description length mismatch: writes accepted empty or up to 16,384 characters while public metadata accepted null or 1–8,000. The draft write contract now uses the same null-or-1–8,000 boundary, with four explicit fixtures. No accepted product requirement authorizes truncating a saved correction.

| Clause | Producer | Required acceptance case |
|---|---|---|
| C12.6.01 | WX-39 | Load each camera/segment and prove plot query bounds equal its owned source interval; do not substitute the calendar day. |
| C12.6.02 | WX-39 | Play, click chart, click keogram and select event; assert the UTC cursor and player position agree in each direction. |
| C12.6.03 | WX-39 | Use nonuniform missing captures and rational frame centers; assert mapping follows the authoritative time map, not array percentage or nearest weather sample. |
| C12.6.04 | WX-39 | Test before start, exact start, last frame and exact exclusive end; retain window-level uncertainty and reject adjacent-segment seeks. |
| C12.6.05 | WX-39 | Inspect visible units, coverage, gaps and aggregate resolution for every plotted metric, including partial coverage. |
| C12.6.06 | WX-39 | Compare the same UTC interval across cameras; station evidence identity/value remains shared and is labeled as measured station data. |
| C12.6.07 | WX-39 | Play across a telemetry gap and a partial/unavailable video; no interpolated measurement or hidden source-time jump is introduced. |
| C12.6.08 | WX-39 | Zoom across midnight and both DST transitions; repeated local times display distinct offsets and stable UTC selection. |
| C12.6.09 | WX-39 | Open copied links in a fresh browser; camera, segment and source selection round-trip including offset-bearing dates. |
| C12.6.10 | WX-39 | Complete equivalent seeks using keyboard/text controls; mobile keeps one active player and enforces the admitted point bound. |
| C12.6.11 | WX-39 | Confirm release acceptance never requires synchronized simultaneous multi-camera playback. |
| C12.7.01 | WX-43 | Publish a valid analysis with no operator session; retain explicit AI and native-video/sampled-frame labels without holding media publication. |
| C12.7.02 | WX-43 | Exercise authorized description/event changes, tags, favorites and poster selection; verify the private interface exposes all five functions. |
| C12.7.03 | WX-43 | Deny unauthorized private reads and writes, invalid CSRF/session requests and spoofed actors; verify the editor DB role cannot perform unrelated administration. |
| C12.7.04 | WX-43 | Browse and play anonymously without account registration or subscription infrastructure. |
| C12.7.05 | WX-43 | Repeat private read/write attempts from LAN and a discovered hidden URL without operator authority; both are denied. |
| C12.7.06 | WX-43 | Rerun AI and edit as a human; immutable automated and human histories remain separately retrievable to authorized operators. |
| C12.7.07 | WX-43 | Expire private raw diagnostics at the qualified short lifecycle while preserving required validated editorial/audit revisions. |
| C12.7.08 | WX-43 | Read audit after each edit; actor, time, reason, old/new revision and input/media/evidence IDs are complete and server-controlled. |
| C12.7.09 | WX-43 | Race two edits on one expected revision; exactly one commits and the other reports conflict without side effects. |
| C12.7.10 | WX-43 | Rerun analysis against unchanged evidence; an active human override and its attribution remain effective. |
| C12.7.11 | WX-43 | Change bound media and measured evidence separately; dependent public/search claims withdraw, old edits stay reviewable and unaffected evidence-bound text remains current. |
| C12.7.12 | WX-43 | Rebase onto changed evidence explicitly; show old/new bindings and require a new audited decision rather than silent approval. |
| C12.7.13 | WX-43 | Reject measured-value mutation through editor fields and event text; public human corrections retain human attribution distinct from validated AI history. |
| C12.7.14 | WX-43 | Inject failure around the transaction and outbox dispatch; active metadata/audit/outbox commit atomically, and replay converges sidecar, cache and index to one revision. |
| C12.7.15 | WX-43 | Hold reindexing and query lexical/vector/typed paths; no old text leaks and current typed/lexical results remain available under the current public revision. |
| C12.7.16 | WX-43 | Set private favorite/note then inspect public API, sidecar, cache, export and search; neither value nor private field appears. |
| C12.7.17 | WX-43 | Select a genuine retained still or qualified decoded published-video frame; reject expired source, invented URL, adjacent-camera identity and unripe preview bypass. |
| C12.7.18 | WX-43 | Exercise automated/human races, revoked identity, rollback, authorized audit viewing, inference outage and isolated restore; preserve revision precedence and privacy. |
| C12.8.01 | WX-44 / OWN-EMBED / WX-33 | WX-44 qualifies the Weather card with a controlled parent fixture; OWN-EMBED then delivers the actual main-site embed, and WX-33 verifies that live integration. An isolated demo cannot close integrated acceptance. |
| C12.8.02 | WX-44 | Test every approved parent origin and an unapproved origin against response CSP; only the card route permits the precise owner-approved ancestors. |
| C12.8.03 | WX-44 | Attempt framing normal pages and private editor routes; neither inherits the card framing exception. |
| C12.8.04 | WX-44 / OWN-EMBED / WX-33 | Verify native order WX-44 → OWN-EMBED → WX-33; the WordPress owner supplies parent source/live evidence before integrated acceptance. Parent delivery is not a prerequisite for constructing its card. |
| C12.8.05 | WX-44 | Compare card and main current-weather responses; visible polling is approximately 15 seconds and measured timestamps/staleness/link remain truthful. |
| C12.8.06 | WX-44 | Hide and restore the view; no overlapping poll accumulation, hidden polling stops where unnecessary and resumption refreshes without a retry burst. |
| C12.8.07 | WX-44 | Load in a fresh cookie-free browser with third-party requests blocked; card works without parent DOM access, privileged cookies, accounts or camera credentials. |
| C12.8.08 | WX-44 | Attempt cross-origin API access and private-route framing from the allowed parent; card framing does not grant broader CORS or admin access. |
| C12.8.09 | WX-44 | Test narrow containers, unavailable/stale data, keyboard and reduced motion, plus allowed and denied parent origins in real browsers. |

All rows remain unimplemented. WX-33 owns integrated qualification; producer issues own implementation and focused evidence. D02/D11/D12/D13/D14/D16 and OWN-EMBED dependencies remain as listed in the ledger. D13 now supplies selected session/framing policy and rendered wireframes; actual provider enrollment, parent delivery, resource admission and browser behavior remain unqualified.

The corrected graph separates card production from owner parent delivery and combined acceptance; the current-weather-only card does not depend on WX-38 camera previews. Issue body hashes in the prior graph receipt remain historical observations after these acceptance additions; the native graph itself is unchanged.
