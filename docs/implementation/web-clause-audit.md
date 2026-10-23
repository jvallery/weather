# Website, routes, playback and cache audit

The [source-bound ledger](web-clause-audit.json) retains every source block and all 19 route rows in §§12.1–12.5. It complements the [38-clause linked-plot/editor/embed audit](ui-clause-audit.md). These grouped acceptance obligations bind implementation owners; they do not prove runtime behavior. WX-33 integrates the complete browser/security surface.

## §12.1 — WX-20, WX-26

- Use the owner-approved React/TypeScript and React Router server-rendered pages/client navigation with same-origin Python projections; prove observations and a new video do not rebuild the complete site/archive.
- Cache immutable assets independently from dynamic projections; framework/runtime and adapter compatibility require exact admitted build pins.
- Match the source-verified theme and preserve font provenance/notices; use the contrast-qualified focus role rather than copying low-contrast gold blindly. Do not import unrelated main-site content.
- Provide compact Weather identity, main-site link and all five navigation entries; wide three-camera grid becomes a mobile stack with reachable date/segment controls.
- Preserve panoramic aspect ratio; instrument initial load to prove posters/lazy metadata avoid eager fetching of six videos.

## §12.2 — WX-19, WX-20, WX-26, WX-39, WX-40, WX-41, WX-43, WX-44

- Implement every route row below and its full source behavior; a schema or placeholder route alone does not satisfy the experience.
- Use one versioned API namespace; proposed shorthand /aggregates resolves to /api/v1/weather/aggregates, and per-video metadata resolves to /api/v1/timelapses/{video_id}.
- Preserve typed filters and selection in shareable URLs and browser back/forward; basic archive and video links work with JavaScript disabled.
- Label overnight with both dates; solar-cycle pages and calendar-day observations/stills remain distinct even across DST.
- Render capturing, queued, processing, published, partial, unavailable and expired deliberately, including empty archive months; no indefinite blank loading card.
- Private editor has authenticated reads/writes and no anonymous mutations; public output never includes private originals or provider keys.

## §12.3 — WX-19, WX-20, WX-39, WX-33

- Use native controls, playsinline, metadata preload, poster and no default autoplay; playing one camera pauses siblings.
- Offer Auto/Web/Mobile/Native and explicit native download dimensions/size; preserve playhead on supported switches and visibly handle unavailable rendition without claiming success.
- Capability advice uses actual codec, geometry and frame rate; retain smaller renditions and prove smooth playback on target devices rather than accepting codec support alone.
- Player source-time display and event/search seek use validated intervals and precision; clip-level evidence must not become exact-second claims. Summaries and measured values are readable without playback.
- Stream bounded bytes and measure memory independently of file size; GET/HEAD, single ranges, 206, lengths, Content-Range, strong validators, conditionals and unsatisfiable 416 all require wire-level acceptance.
- Reject or explicitly implement multi-range requests without amplification; attachment uses safe filename and identical native bytes, with no Garage/presigned redirects.
- Test Safari/iOS, Chrome/Android and desktop Safari/Chrome/Firefox; seek near EOF, interrupted/resumed downloads, quality changes and reduced-data preferences on the actual weather-domain route.

## §12.4 — WX-19, WX-27, WX-30, WX-33

- Use immutable media/poster cache policy and explicit bounded catalog/current/search freshness and validators; private backup/raw/admin content never enters shared/CDN caches.
- Cache lookup cannot publish an unlisted asset; withdrawal must cover origin and existing caches with bounded purge behavior, not only DB tombstones.
- Keep viable weather-domain delivery without optional CDN; qualify actual plan, video terms and ingress. Disabling cache does not resolve unapproved tunnel/video transport.
- Do not replace canonical Garage with R2/Stream or purchase products implicitly; optional CDN work remains conditional while required playback remains required.
- Revalidate actual account object-size limits and measured rendition sizes; range size is not full-object cache eligibility. Preserve validators/lengths and prove Safari MP4 behavior.
- TTL is freshness, not residency. Exercise miss/eviction/origin outage and withdrawal rather than assuming a long TTL supplies availability.

## §12.5 — WX-20, WX-26, WX-33, WX-39, WX-43, WX-44

- Test keyboard order, visible focus, labels, readable contrast, reduced motion and approximately 44 px touch targets across all public/private/embed controls.
- Render at 320/390/768 px and desktop with long summaries, no data and expired/unavailable assets; panorama and controls remain usable without horizontal page overflow.
- Announce meaningful state transitions without rereading every 15-second poll; charts provide text/summary alternatives and explicit missing-data gaps.
- Self-host all fonts/icons/scripts/player assets and preserve distribution notices; no external analytics/weather widgets or accidental third-party network dependencies.
- Qualify restrictive CSP with the actual built assets; escape description/model/editor text and exercise malicious input without granting broad script exceptions.
- Inspect generated bundles, hydration, public source maps and error pages for credentials, internal endpoints, raw source payloads and private infrastructure.

## Route ownership

Each producer must deliver the complete behavior preserved in the ledger, including all stated provenance, coverage, state and navigation details. Supporting services retain their graph prerequisites.

| Route or surface | Producer |
|---|---|
| `/` | WX-20 |
| `/archive/` and month views | WX-20 |
| `/day/YYYY-MM-DD/` | WX-20, WX-39, WX-40 |
| `/timelapses/<camera>/<date>/<segment>/` | WX-20, WX-39 |
| `/stills/` and camera/date views | WX-37 |
| `/weather/history/` | WX-20, WX-41 |
| `/weather/almanac/` | WX-41 |
| `/events/` | WX-40 |
| `/search/` | WX-26, WX-42 |
| `/embed/weather/` | WX-44 |
| `/about/` | WX-20 |
| `/api/v1/weather/current` | WX-13 |
| `/api/v1/weather/observations`, `/aggregates` | WX-13, WX-41 |
| `/api/v1/timelapses` and per-video metadata | WX-17 |
| `/api/v1/stills`, `/api/v1/cameras/<camera>/preview`, `/api/v1/keograms` | WX-37, WX-38, WX-36 |
| `/api/v1/events`, `/api/v1/weather/almanac` | WX-40, WX-41 |
| `/api/v1/search` | WX-25, WX-42 |
| `/media/<asset-id>` and `/downloads/<video-id>/native` | WX-19 |
| Private editorial UI/API | WX-43 |

## Evidence limits and planning dependencies

D13 selects the framework and layout policy and records live/source theme evidence; the separate font review resolves provenance. Actual build pins and rendered responsive wireframes remain missing. The existing OpenAPI declares all required API families, with the consistent full aggregates path and explicit per-video metadata route; that proves interface inventory only, not handlers, bounding, authorization or browser integration.

D14/E07 establishes the observed account/tunnel situation, not a permitted production video route. OWN-EDGE must resolve required origin delivery independently of conditional WX-27. Exact account limits must be rechecked during qualification; the requirement’s documented size figures are not a current entitlement. D16 establishes product clocks but does not qualify cache withdrawal bounds or playback throughput. Source declarations cannot prove already-cached bytes were recalled.

No new dependency edge is needed: existing producers and integrated acceptance already participate in the initial rollout closure. No route, CDN setting, private identity or application code was deployed by this audit.
