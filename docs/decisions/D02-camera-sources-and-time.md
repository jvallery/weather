# D02 — native camera snapshots and qualified source time

Status: **three snapshot endpoints verified; full-view/no-mask public policy approved; sustained cadence, source-time calibration and keogram-region qualification pending**. Requirements R03–R06/R08/R30–R32; WX-09/10/36/37/38/39. Camera ownership and commissioning are tracked by [OWN-CAM](https://github.com/jvallery/sentinel/issues/550); no camera settings or NVR deployment changed.

## Selected adapter and E06

Use the fixed-camera channel-1 snapshot API with Digest authentication, scoped credentials and no redirects. Prefer verified HTTPS where the device's qualified configuration supports it; otherwise use the approved camera-network HTTP path. Collector destinations are fixed private configuration, never URLs supplied by public clients. Do not use Frigate's latest-frame endpoint, retained video extraction or a persistent RTSP decoder as the acquisition source.

On 2026-10-23, sequential bounded authenticated GETs returned decodable JPEGs from all three selected cameras. Exact addresses, source revision, payload hashes and request metadata remain in the private E06 receipt; images are retained only on the private operator surface.

| Camera | Successful JPEG dimensions | Bytes | Response elapsed seconds | Observed limitation |
|---|---|---:|---:|---|
| skycam_east | 4096 × 1856 | 2,060,757 | 0.662 | First request timed out with a one-second socket timeout; one retry with three seconds succeeded. |
| skycam_west | 4096 × 1856 | 1,614,699 | 1.253 | One sample; elapsed includes authentication/transfer, not a sustained-rate measurement. |
| frontyard_1 | 4096 × 1856 | 3,295,643 | 0.525 | One sample; byte size is scene/encoding dependent. |

E06 receipt SHA-256: `4bb04638b879a02ebbc54f0cce67b4829e1dd9c287ecc2223292168862414418`. The initial probe's missing optional HTTP client library caused no camera request; the completed probes used the standard-library Digest client. JPEGs were fully decoded for this small bounded check. No request loop, rate test, camera write, credential change or media publication occurred.

These dimensions establish the observed JPEG output, not independently verified maximum native sensor/main-stream parity. Current source configuration uses smaller Frigate detect geometry for the sky cameras; that does not constrain Weather's observed snapshot dimensions or authorize changing Frigate. Device model/firmware, full native-mode parity, NVR impact and source timestamp trust remain owner qualification items.

## Capacity consequence

The three successful samples total 6,971,099 bytes. If those exact sizes occurred continuously at one sample/second per camera, payload alone would be about 602.3 decimal GB/day. Forty-eight hours plus 30% reserve would be about 1.566 TB before scratch, metadata and other NAS obligations. This is a sample-based arithmetic illustration, not measured daily load, p95 sizing or a requested grant. It shows why the earlier 0.5 MB example cannot determine admission. OWN-NFS must use commissioned per-camera distributions, backlog and scratch demand.

Preserve the 1 Hz commissioning target with one in-flight request per camera, bounded total deadlines, staggered phases and skipped overdue slots. A response exceeding a second does not trigger overlapping requests or a catch-up burst. Qualify connect/read/total deadlines and the 16 MiB initial payload ceiling against all lighting regimes. Rate/size limits must fail visibly; do not lower camera resolution or omit native originals to fit a presumed budget.

## Time and derived products

Use the [capture contract](../contracts/capture-contract.md) for slot, wall-clock and monotonic epochs, source-time estimate, uncertainty, freshness evidence and manifest ownership. HTTP Date and arrival time do not establish exposure time; equal JPEG hashes alone do not establish staleness. A camera timestamp needs validation; otherwise measure caching/latency and calibrate the request-midpoint estimate. Record unbounded uncertainty honestly and deny public image eligibility while its latest plausible exposure cannot be trusted.

Freeze local timezone/site and civil-twilight model revisions for each cycle. Assign observations to one half-open segment by qualified source time, independently of request slot. Calendar-day/hour identities use actual elapsed local hours, with repeated offsets preserved; hourly selection can cross twilight while retaining its own source pins. No temporal render context crosses a segment merely because adjacent captures share a TAR. Keep exact 7,200-frame/60-fps cycle allocation and the accepted UTC/local-label distinction.

Keogram regions and horizon exclusions must be versioned per camera after day/twilight/night qualification. The owner-approved public visual-mask policy is explicitly none for all three full views, as recorded below. Do not reuse Frigate motion/detection masks as privacy policy or assume one sky strip suits the frontyard camera. Preserve genuine source lineage, missing bins and accessible source-time seeking. Public derivatives strip private metadata and enforce the latest plausible exposure bound plus 600 seconds; private originals remain inaccessible from public media routes.

## Acceptance and rollback

OWN-CAM/WX-10 require admitted day/twilight/night samples of latency, byte/geometry distributions, actual fresh-frame rate, clock/cache behavior, errors and existing NVR health before/during/after collection. Test stale responses, malformed/oversized JPEGs, Digest failure, slow-trickle bodies, no redirects, timeout, restart/clock discontinuity and no catch-up. A one-second probe timeout is evidence to tune commissioning, not proof the camera is unusable.

WX-09/36/37/38/39 validate boundary/DST/hour identity, per-camera regions, retained original bytes, eligibility and linked navigation with those qualified inputs. Missing/failed cameras remain explicit; no fictional frame or silent alternate source substitutes. Stop a trial by stopping its exact collector, preserve receipts and verify unchanged camera/NVR behavior. No camera rollback should be necessary because collection makes no management writes.

Source exposure bounds do not qualify the authorizing host clock. The [conservative clock contract](../contracts/preview-policy-contract.md#conservative-authorization-clock-bounds) requires exposure upper +600 seconds <= authorizer now lower, and now upper strictly before logical expiry. Keep source calibration and host-clock validity independently bound; unknown or stale clock evidence denies public eligibility. Numeric clock admission and actual adapter behavior remain unqualified.

## Owner-approved public image policy

After review of the retained E06 daylight samples, Jason explicitly selected **“Use all three full views without visual masks.”** Apply this to `skycam_east`, `skycam_west` and `frontyard_1` public timelapses, sanitized comparison stills, delayed previews, video posters and analysis inputs. Preserve the full selected camera field of view and native aspect ratio; do not add privacy crops, opaque regions or blur by inference. This resolves the camera-mask owner question for the reviewed views. No image was uploaded to the public repository or deployed by this planning decision.

Record an explicit versioned `none` visual-mask policy per camera, bound to the selected source geometry/orientation and derivative recipe. An empty mask is an intentional owner selection, not missing configuration. Keogram extraction still selects a weather-relevant sampling region and records that region separately; it is a derived visualization rather than an implicit privacy crop. Existing video rendition/geometry contracts remain unchanged.

Full-view approval does not make native originals public. Keep archived original JPEG bytes private and publish through the derivative validation path, stripping private metadata and excluding exact coordinates, addresses, station identities and credentials from serializers and sidecars. Preserve the minimum 600-second eligibility delay from the latest plausible exposure and current expiry/withdrawal rules. Approved full views may contain household activity; do not add people/object redaction that Jason did not request. Camera-rendered timestamps are image pixels, not proof of trustworthy exposure time; existing source-time calibration still applies.

The policy is bound to the reviewed camera setup. Detect an unexpected source/geometry/orientation change as a configuration mismatch rather than silently treating it as the same approved recipe; follow normal source-change qualification. If Jason later selects masks, bind the new policy revision through every derived output and model input and withdraw incompatible public derivatives using D14. No such mask change is selected now.

Acceptance: verify all three cameras use the explicit no-mask policy, full-view/aspect-ratio preservation, private-original access denial, derivative metadata stripping and unchanged delay/expiry/withdrawal checks. The retained single daylight samples establish the reviewed scene only; they do not prove sustained cadence, night behavior, exposure-time calibration or renderer implementation. Those remain the existing commissioning obligations.
