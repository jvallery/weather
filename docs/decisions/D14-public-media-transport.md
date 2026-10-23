# D14 — Public media transport and withdrawal

Status: Cloudflare Tunnel selected by Jason for public pages, API, video playback and native downloads; implementation qualification remains. Requirements §§11.3–11.5, 12.3–12.5 and 18.2; R11/R18/R25/R27/R37. Consumers WX-19 and conditional WX-27; network prerequisite OWN-EDGE (#775). This decision does not authorize production routing or a provider purchase.

## Evidence and implication

E07, observed 2026-10-23, read the active zone through the existing network capability broker: the complete single-zone response reports Free Website. The live tunnel configuration contains a wildcard covering the Weather hostname, and the hostname resolves to Cloudflare addresses. The edge Application reports Synced/Healthy at a revision distinct from the inspected owner main revision. These observations establish current routing surfaces, not a deployed Weather application, streaming performance, or permission to deliver video. The zone result is not an inventory of paid account products or negotiated exceptions. Private metadata and exact revisions belong in OWN-EDGE; public source retains this sanitized conclusion.

## Owner decision — Cloudflare Tunnel

Jason explicitly selected the existing Cloudflare Tunnel approach, citing successful Frigate streaming and declining further Cloudflare investigation. This decision supersedes the prior direct-HTTPS versus paid-transport planning question and the provider-qualification planning gate. Keep tunnel-only inbound. Do not investigate or introduce direct WAN forwarding, paid video products, R2 or Stream as part of this design.

The earlier provider-documentation review is historical evidence, not a remaining owner question or a reason to block this selected architecture. This records the owner's design direction; it does not claim a changed provider entitlement or that Weather playback has already been tested.

E13 observed the named WAN HTTP/HTTPS/MCP forwards disabled, with incomplete broker pagination. That limited observation remains valid but no longer requires further direct-ingress feasibility research. Exact evidence stays in private OWN-EDGE. No DNS, firewall, provider or service configuration changed.

## Selected route and responsibility

Keep one browser origin for pages, API, playback and native downloads through the existing Cloudflare Tunnel and estate ingress. Garage remains canonical, accessed through D08; browsers never receive provider credentials or redirected object URLs. OWN-EDGE defines the scoped hostname-to-ingress route and edge configuration; Weather owns its namespace service routes and application behavior. Preserve private-original isolation and operator authentication.

Optional CDN caching remains WX-27 and must not be required for playback. Start with shared media caching disabled; retain validators and bounded origin streaming. Any later cache configuration must meet the privacy, expiry and withdrawal contract below. Weather-specific route, range, browser, download and load verification belongs to implementation acceptance, not another transport-selection exercise. Set finite stream/resource limits against the owner allocation before enabling production traffic.

## Application and edge contract

The origin resolves an opaque asset ID to a committed immutable object and checks visibility, class, expiry and any embargo before headers, validators or bytes. GET, HEAD, conditional requests and ranges share that authorization path. Rejected or unknown IDs cannot disclose private object metadata. D08 separately enforces original/backup isolation. Edge authentication protects operator routes without putting public playback behind an operator login.

Stream bounded chunks with backpressure and cancellation; propagate disconnects upstream and close the object response. Admit finite concurrent streams and bound buffers, open connections, header/body timeouts and request rates. Return explicit overload/unavailable responses before body start when possible. After a mid-stream failure, terminate the incomplete transfer rather than append an error page to video bytes. Exact numeric limits require the owner bandwidth and workload budget; no unbounded retry or whole-file buffering.

Single-range responses must preserve byte identity, strong validators, correct status, length and Content-Range. Apply conditional preconditions before range handling; If-Range mismatch returns the complete current representation. Pin the resolved immutable revision for the entire request. The [selected HTTP contract](../contracts/api-contract.md#selected-precondition-and-range-policy) rejects multiple byte ranges with 400, ignores ranges on HEAD, and specifies precondition ordering, 412, suffix bounds and If-Range fallback. Use bounded parsing with no multipart amplification. HEAD emits no body. Safe attachment names and MIME types are server-generated; downloads return the exact native rendition. No content transformation/compression may invalidate byte offsets, lengths or validators.

Publication caches can store only eligible public derivatives. A cache miss cannot bypass origin checks. Private originals, backups, unpublished/unripe products and operator responses never enter shared caches. Immutable object bytes do not imply indefinite public visibility: expiration, correction and withdrawal require a bounded removal policy across origin, aliases and every selected cache. Finalize the withdrawal SLA and cache TTL/purge contract with D16 before enabling long-lived caching. Already downloaded bytes cannot be recalled; do not promise otherwise.

## Qualification and rollback

WX-19 must verify external GET/HEAD, satisfiable and unsatisfiable single ranges, validators/If-Range, correct lengths, end-of-file seek, interrupted/resumed native downloads, slow-client cancellation, overload, origin failure and withdrawal. Cover Safari/iOS, Chrome/Android and desktop Safari/Chrome/Firefox using actual encoded geometry and codecs. Verify all asset aliases, guessed IDs and private-class negative cases, including cached responses and requests at the embargo boundary. Bind evidence to the exact route, application digest and provider configuration; an internal curl or healthy edge is insufficient.

WX-27 adds cache hit/miss, purge, range and oversized-object tests if caching is enabled. OWN-EDGE supplies the scoped Tunnel route configuration and resource budget; Weather owns application semantics. Rollback restores the exact scoped route/configuration while preserving object and catalog state. If the selected route fails, expose honest unavailability; never silently fail over to direct ingress or public Garage access. No DNS, tunnel, firewall or service configuration changed during E07.
