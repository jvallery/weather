# Weather — open-source landscape and product decisions

**Research date:** 2026-10-23  
**Status:** research complete; all Q01–Q16 answered and incorporated into requirements v4.0  
**Baseline reviewed:** [Weather requirements v3.0](https://github.com/jvallery/weather/blob/0126db40f394e0a5d802e34369a12552912e5faf/docs/requirements.md)  
**Current authority:** [requirements v4.0](../requirements.md) and [accepted owner decisions](../decisions/2026-10-23-product-scope.md)  
**Audience:** project owner and Codex performing the implementation research pass

## Contents

- [Recommendation](#1-recommendation)
- [Research method](#2-research-method-and-interpretation)
- [Project comparison](#3-project-comparison)
- [Requirement fit](#4-where-the-existing-requirements-remain-distinctive)
- [Feature proposals](#5-features-worth-adding-or-explicitly-reserving)
- [Proposed design changes](#6-proposed-design-changes-subject-to-answers)
- [Owner questionnaire](#7-questions-for-the-owner)
- [Codex follow-through](#8-codex-follow-through-for-the-selected-direction)
- [Evidence ledger](#9-evidence-and-revision-ledger)

## 1. Recommendation

Keep `jvallery/weather` as the independently owned application, and reuse narrowly scoped libraries, algorithms, and interface ideas where they pass the existing contracts. None of the projects reviewed establishes the complete combination of native snapshot acquisition, exact civil-twilight partitions, the 120-second/60-fps daily pair, timestamp-aware interpolation and shutter integration, complete local Tempest telemetry, CNPG, private Garage delivery, and weather-grounded video analysis/search.

The review considered the following credible alternatives before the owner selected an independent implementation. These remain reference comparisons, not active fork proposals:

- **Glimpser** is the most interesting whole-application prototype because camera acquisition, timelapses, image descriptions, and summaries already coexist. Its actual media and AI paths differ substantially from ours.
- **Stormglass** is the strongest candidate for a limited weather-service fork. It already receives local Tempest UDP and writes PostgreSQL, but its dashboard's historical reader still depends on SQLite, and its telemetry model is not a complete raw-packet archive.
- **indi-allsky** is the closest established sky-camera archive architecture. It has HTTP image acquisition, solar day/night transitions, historical galleries, and S3 uploads. Its astronomy-oriented processing and persistence would still need significant adaptation.

The owner selected independent Weather development with selective references/reuse. No bounded application-fork experiment is required. [Requirements §20.1](../requirements.md#201-selected-implementation-references-without-an-application-fork) maps the reviewed solutions to concrete work and the contracts that must survive any reuse. Section 8 gives the selected follow-through.

The original comparisons, proposals and option wording below are retained as research history. The [accepted decision record](../decisions/2026-10-23-product-scope.md) supersedes their recommendations, and the changes are incorporated into requirements v4.0 and the candidate graph. This publication does not claim actual implementation issues are filed. Cluster questions remain Codex's responsibility; they are not delegated back to the owner.

## 2. Research method and interpretation

The review used broad web searches, project documentation, current GitHub repository/release metadata, and targeted source inspection. It covered camera/timelapse applications, all-sky imaging systems, Tempest collectors and dashboards, media libraries, semantic search, and interactive time-series video viewers. The most important findings were checked below README level: scheduler intervals, camera adapters, actual database readers/writers, packet parsing, video encoders, and AI request construction.

This is a source-and-design review. The projects were not installed against the cameras, benchmarked on the nine GPUs, subjected to a security audit, or validated against the live cluster. A documented feature is not a proven operational fit. Release dates establish recent activity, not maintenance quality or correctness. A missing capability means it was not established in the reviewed paths; it is not a claim that nobody could add it.

License labels come from repository license files/metadata where available. Before copying code, Codex must record the exact revision, relevant file notices, dependency/model/asset licenses, and the selected project license. Public visibility alone does not establish an open-source license. The `snapshotr` README/license discrepancy and the unlicensed `webcam-archives` candidate are called out explicitly below.

## 3. Project comparison

### Shortlist

“Best role” is this review's recommendation. Release dates are observed GitHub releases as of the research date, not promises of support. Detailed revision pins appear in section 9.

| Project | License / recent release observed | Existing strengths | Best role for Weather |
| --- | --- | --- | --- |
| [indi-allsky](https://github.com/aaronwmorris/indi-allsky) | GPLv3; September 2026 packaged release and October nightly | HTTP cameras, solar day/night operation, sky products, database gallery, S3 transfers | Strongest existing sky/archive foundation; evaluate a fork only if its broader sky features are central. |
| [Glimpser](https://github.com/KristopherKubicki/glimpser) | MIT; v0.2.10, 2026-10-01 | Camera snapshots, timelapses, captions, summaries, history UI | Strong bounded application-fork experiment; major media/AI/storage differences remain. |
| [Stormglass](https://github.com/jacaudi/stormglass) | MIT license file; v1.1.1, 2026-10-17 | Direct Tempest UDP, PostgreSQL writes, embedded dashboard, metrics | Strongest weather-service fork experiment or source of selected components. |
| [ReoLapse](https://github.com/SeriesOfTubez/reolapse) | MIT; v0.5.1, 2026-10-13 | Daily, seasonal and event videos; simple snapshot/render/archive workflow | Capture/publication workflow and feature inspiration. |
| [ChronoSnap](https://github.com/kernelkaribou/chronosnap) | MIT; v3.8.0, 2026-09-10 | Generic camera jobs, comparisons, favorites, import/export, PWA, API | Private management UI/API reference. |
| [Allsky](https://github.com/AllskyTeam/allsky) | MIT; v2026.10.01, 2026-10-01 | Keograms, keolapse, star trails, overlays, public sky pages | Reusable sky-product algorithms and presentation concepts. |
| [WeeWX](https://github.com/weewx/weewx) + [Belchertown](https://github.com/poblabs/weewx-belchertown) | GPLv3; 5.5.2 on 2026-09-23 / 3.0.0 on 2026-10-08 | Established station reporting, calculated metrics, records, charts and almanac | Weather-domain and reporting reference; a different persistence backbone. |
| [StormDesk](https://github.com/d4vid87/stormdesk) | MIT; stable 4.5.2, 2026-10-07; separate 5.0 previews | Rich local dashboard, native/headless UDP service, historical views and exports | Selected UI/modules; current upstream preferred over the older WeatherDesk fork. |
| [AviationWX](https://github.com/alexwitherspoon/aviationwx) | MIT; reviewed main updated 2026-10-23 | Public weather and cameras, historical image player, embeds, kiosk and stale-state handling | Closest public weather-plus-camera presentation reference. |
| [Frigate](https://github.com/blakeblackshear/frigate) | MIT; v0.18.2, 2026-10-15 | Search, evidence cards, review, timelines and exports | Search-to-evidence interaction reference; preserve the independent existing NVR. |
| [Immich](https://github.com/immich-app/immich) | AGPL-3.0; v3.3.5, 2026-10-21 | Calendar/gallery, renditions, metadata filters and visual similarity | Gallery/search ideas; optional secondary export, not the canonical weather platform. |
| [CMU Time Machine](https://github.com/CMU-CREATE-Lab/timemachine-viewer) | Permissive CMU license text; viewer v1.0 in 2020, later source activity | Spatial/time navigation over tiled timelapse datasets | Optional viewer prototype if deep panorama exploration becomes a priority. |

### indi-allsky: the strongest sky-camera archive comparison

This is more relevant than its astronomy branding first suggests. Its [HTTP camera adapter](https://github.com/aaronwmorris/indi-allsky/blob/18b8407d31843f3df9c69f8b5b16443f7df65c82/indi_allsky/camera/pycurl_camera.py) downloads images with pycurl and supports configurable credentials and `HTTPAUTH_ANY`. This makes an authenticated IP-camera snapshot path plausible without continuous RTSP. Its [configuration](https://github.com/aaronwmorris/indi-allsky/blob/18b8407d31843f3df9c69f8b5b16443f7df65c82/indi_allsky/config.py) uses a default −6° night threshold, and its [capture process](https://github.com/aaronwmorris/indi-allsky/blob/18b8407d31843f3df9c69f8b5b16443f7df65c82/indi_allsky/capture.py) creates day/night products at transitions.

The [project](https://github.com/aaronwmorris/indi-allsky/tree/18b8407d31843f3df9c69f8b5b16443f7df65c82) already offers a Flask gallery, thumbnails, keograms, star trails, sky charts and remote synchronization. Its documented databases are SQLite and MySQL/MariaDB. [S3 uploads are implemented](https://github.com/aaronwmorris/indi-allsky/blob/18b8407d31843f3df9c69f8b5b16443f7df65c82/indi_allsky/filetransfer/boto3_s3.py), not just proposed.

The gap is the operational core. The [timelapse renderer](https://github.com/aaronwmorris/indi-allsky/blob/18b8407d31843f3df9c69f8b5b16443f7df65c82/indi_allsky/timelapse.py) builds a numbered image sequence with uniform input FPS. Its [video worker](https://github.com/aaronwmorris/indi-allsky/blob/18b8407d31843f3df9c69f8b5b16443f7df65c82/indi_allsky/video.py) couples processing, ORM records, task state, uploads and expiration. Adopting it would require our exact UTC boundaries and frame budget, target-cadence validation, CNPG adaptation, collector isolation, durable publication/cleanup, complete Tempest history, AI/search and joint recovery. The current HTTP adapter also has redirect and TLS-verification defaults that should not be inherited into the restricted collector.

**Verdict:** a credible fork candidate if the product expands toward a comprehensive sky observatory. For the present service, the cost is substantial enough that selective algorithms/UI ideas are the preferred starting point. Its GPL license needs an explicit reuse decision; it is not interchangeable with Allsky's MIT license.

### Glimpser: the closest application, with substantial pipeline changes

[Glimpser v0.2.10](https://github.com/KristopherKubicki/glimpser/releases/tag/v0.2.10), released October 1, 2026, is the closest application-level match: multi-source capture, timelapse history, captions, summaries, and a weather-facing presentation. Its [MIT license](https://github.com/KristopherKubicki/glimpser/blob/9a62aba11f6aff2858e2794b1e9ff9ca4047e7a9/LICENSE.md) permits a fork or selective reuse with the required notices. Its [package metadata](https://github.com/KristopherKubicki/glimpser/blob/9a62aba11f6aff2858e2794b1e9ff9ca4047e7a9/pyproject.toml) still identifies it as alpha.

The implementation differs materially from this project's requirements. Its [documented analysis flow](https://github.com/KristopherKubicki/glimpser/blob/9a62aba11f6aff2858e2794b1e9ff9ca4047e7a9/docs/llm_summarization.md) captions screenshots and summarizes the resulting text. [Provider code](https://github.com/KristopherKubicki/glimpser/blob/9a62aba11f6aff2858e2794b1e9ff9ca4047e7a9/app/utils/llm.py) defaults to OpenAI cloud requests with an optional local fallback. It does not establish completed-video analysis through the required local GPUStack route. The reviewed [video archiver](https://github.com/KristopherKubicki/glimpser/blob/9a62aba11f6aff2858e2794b1e9ff9ca4047e7a9/app/utils/video_archiver.py) uses 25 fps image input and a 1280×720, 30 fps output filter, with optional deflicker. Native-aspect 60 fps rendering and authoritative exposure/time mapping would need replacement or extension.

**Fork verdict:** the strongest candidate for a bounded prototype, if existing UI saves substantial work. Continue only if the bounded experiment establishes a credible design for snapshot acquisition, Garage/CNPG integration, completed-video analysis, semantic indexing, and isolated background jobs; unresolved implementation proofs become rollout-blocking issues under section 8. Otherwise reuse selected helpers and history interactions. A feature-rich prototype should not bypass these acceptance gates.


### ReoLapse: especially good ideas for seasons and weather events

ReoLapse's [feature set](https://github.com/SeriesOfTubez/reolapse/tree/2ae0589f7449296340389ee6abfd0c15228513b8) is close to the original problem: daily videos, yearly seasonal videos, weather/lunar events, browser playback and downloads. Its small Python capture/render/web separation is understandable. However, its [capture path](https://github.com/SeriesOfTubez/reolapse/blob/2ae0589f7449296340389ee6abfd0c15228513b8/capture.py) uses the Reolink Snap API and enforces a **ten-second minimum interval**. It is not a ready one-hertz Amcrest collector.

The [renderer](https://github.com/SeriesOfTubez/reolapse/blob/2ae0589f7449296340389ee6abfd0c15228513b8/build_timelapse.py) provides worthwhile practices: temporary outputs, frame-count checks, atomic promotion, and deflicker. Event profiles can avoid deflicker so real flashes are less likely to be suppressed. Yet its uniform numbered frames and local-time filenames need replacement for timestamp-aware rendering, DST-safe identity and exact duration allocation.

The [event implementation](https://github.com/SeriesOfTubez/reolapse/blob/2ae0589f7449296340389ee6abfd0c15228513b8/events.py) uses external weather sources, whereas our event decisions should use local Tempest observations and labeled visual evidence. Its object-storage support is a roadmap item in the reviewed version, and its web administration is explicitly LAN-oriented.

**Verdict:** borrow the seasonal/event product ideas and failure-handling patterns. A full fork would replace much of the camera, timing, rendering, weather and storage logic that makes the small application useful.

### ChronoSnap: a strong management interface, with a coarse capture loop

ChronoSnap offers useful [job management](https://github.com/kernelkaribou/chronosnap/tree/01b055cb102ce60d226ce9e0b8ba93ce102aae73): HTTP/RTSP/USB sources, scheduled builds, tags, favorites, side-by-side comparisons, metadata-preserving import/export, a PWA and FastAPI/OpenAPI. It is a good reference for the private operations UI.

Two source details materially affect fit. Its [scheduler](https://github.com/kernelkaribou/chronosnap/blob/01b055cb102ce60d226ce9e0b8ba93ce102aae73/backend/services/capture_scheduler.py) waits for submitted work and sleeps ten seconds per cycle. Its [HTTP capture](https://github.com/kernelkaribou/chronosnap/blob/01b055cb102ce60d226ce9e0b8ba93ce102aae73/backend/services/image_capture.py) launches FFmpeg to output a new JPEG. The reviewed path therefore does not establish one-hertz native-byte snapshot acquisition, even though the UI accepts intervals expressed in seconds.

The [database layer](https://github.com/kernelkaribou/chronosnap/blob/01b055cb102ce60d226ce9e0b8ba93ce102aae73/backend/database.py) directly uses SQLite; the [renderer](https://github.com/kernelkaribou/chronosnap/blob/01b055cb102ce60d226ce9e0b8ba93ce102aae73/backend/services/video_processor.py) assigns equal durations to images. Also, [v3.7.0 removed unauthenticated public sharing](https://github.com/kernelkaribou/chronosnap/releases/tag/v3.7.0), so older public-gallery descriptions should not be treated as a current feature.

**Verdict:** a useful private timelapse manager or source of interface ideas. A Weather fork needs new capture scheduling, time semantics, persistence and public publication boundaries.

### Allsky: useful derived-media components

[Allsky](https://github.com/AllskyTeam/allsky/tree/2b2c7b1347d076a489df5e614acf27de450738fb) is a different project from indi-allsky. It has a long-standing Raspberry Pi sky-camera workflow, public images, overlays, star trails, keograms and newer keolapse products. Its documented acquisition is centered on Raspberry Pi/CSI and ZWO cameras; adopting the full appliance for three HTTP security cameras would introduce unnecessary platform work.

The [MIT OpenCV keogram utility](https://github.com/AllskyTeam/allsky/blob/v2026.10.01/src/keogram.cpp) is a more promising reuse boundary. The inspected implementation positions columns by input-file index. Our version should instead position them on an elapsed-time grid and mark gaps. Configurable sky regions matter for panoramic cameras; a center strip can miss interesting weather elsewhere. Pairing the strip with a small whole-frame contact sheet would help.

Its [solar integration](https://github.com/AllskyTeam/allsky/blob/2b2c7b1347d076a489df5e614acf27de450738fb/src/allsky_common.cpp) supports a configurable angle, including civil twilight, but that does not establish our exact half-open interval and time-map contract. Its [movie script](https://github.com/AllskyTeam/allsky/blob/2b2c7b1347d076a489df5e614acf27de450738fb/scripts/timelapse.sh) still uses a uniform image sequence.

**Verdict:** strong source of small sky-product components. Star counts, star trails and sky-brightness estimates should first be tested against these cameras' real nighttime images, exposure behavior and fields of view.

### Stormglass: the most promising Tempest subsystem fork

Stormglass is a Go service with a vendored React dashboard, direct local Tempest UDP, metrics, and both SQLite and PostgreSQL writers. Its [core local mode](https://github.com/jacaudi/stormglass/blob/ffa4b40f2472f8248660b4386910131128121ec5/README.md) does not need a WeatherFlow account. This makes it substantially closer to our data path than a cloud-backed station dashboard.

The key finding is that **PostgreSQL storage is not yet a PostgreSQL-only dashboard**. The [API startup wiring](https://github.com/jacaudi/stormglass/blob/ffa4b40f2472f8248660b4386910131128121ec5/cmd/stormglass/main.go) passes a SQLite reader; [HTTP read types](https://github.com/jacaudi/stormglass/blob/ffa4b40f2472f8248660b4386910131128121ec5/internal/httpserver/observations.go) and the [PostgreSQL schema comments](https://github.com/jacaudi/stormglass/blob/ffa4b40f2472f8248660b4386910131128121ec5/internal/postgres/schema.go) confirm the dependency. A PostgreSQL-only configuration does not provide that observation reader.

Its raw-data terminology also differs from ours. The [parser](https://github.com/jacaudi/stormglass/blob/ffa4b40f2472f8248660b4386910131128121ec5/internal/tempestudp/report.go) accepts known packet families, and the schema stores selected typed measurements. It does not preserve every original datagram or arbitrary field. The [PostgreSQL writer](https://github.com/jacaudi/stormglass/blob/ffa4b40f2472f8248660b4386910131128121ec5/internal/postgres/writer.go) drops batches after bounded retry exhaustion. We still need a durable receipt/spool/replay layer and a complete all-row parser contract.

The [frontend polling](https://github.com/jacaudi/stormglass/blob/ffa4b40f2472f8248660b4386910131128121ec5/web/src/hooks/useWeatherData.ts) is every 30 seconds, so the existence of rapid-wind storage does not imply a three-second live display. The [optional-feature documentation](https://github.com/jacaudi/stormglass/blob/ffa4b40f2472f8248660b4386910131128121ec5/docs/dashboard.md) also distinguishes implemented almanac/radar features from an unwired forecast card.

**Verdict:** a meaningful component fork is plausible. Demonstrate a CNPG-only reader and durable full-packet custody first. If that works cleanly, its parser/tests, formatting, card components, degraded states and metrics may save effort. It still does not provide the camera/media/AI half of Weather.

### WeeWX and Belchertown: the strongest conventional station-reporting reference

Both projects are active: WeeWX released [5.5.2](https://github.com/weewx/weewx/releases/tag/v5.5.2) in September 2026, and original Belchertown released [3.0.0](https://github.com/poblabs/weewx-belchertown/releases/tag/weewx-belchertown-3.0.0) in October. Old statements that Belchertown stopped in 2023 are no longer a sound basis for this decision.

WeeWX's [database documentation](https://weewx.com/docs/5.5/reference/weewx-options/database-types/) describes SQLite and MySQL; its [standard archive](https://weewx.com/docs/5.5/reference/weewx-options/stdarchive/) normally consolidates observations into five-minute records. A [WeatherFlow UDP driver](https://github.com/captain-coredump/weatherflow-udp/blob/eedfaf7b1ee1d0eb51513966b7a09e7622858791/bin/user/weatherflowudp.py) exists, but maps selected fields and the first observation row into WeeWX's measurement model. This does not satisfy original-packet retention or imply a PostgreSQL port.

The product value is considerable: [derived measurements](https://weewx.com/docs/5.5/reference/weewx-options/stdwxcalculate/), configurable charts, [Belchertown records and almanac](https://github.com/poblabs/weewx-belchertown/blob/049f615952da8d9e0c779ccab9e6f64cf88fedf6/README.md), precipitation summaries and live updates through optional MQTT. The [browser template](https://github.com/poblabs/weewx-belchertown/blob/049f615952da8d9e0c779ccab9e6f64cf88fedf6/skins/Belchertown/header.html.tmpl) currently references public CDN libraries, including separately licensed Highcharts, so its delivery/dependency model must be adapted.

**Verdict:** excellent weather-domain inspiration. A full adoption would require a PostgreSQL implementation or a second reporting store plus reconciliation. That is less attractive than using CNPG as the existing canonical history and reproducing the desired reporting features.

### StormDesk and the older WeatherDesk fork: rich views, partial telemetry history

Current [StormDesk](https://github.com/d4vid87/stormdesk/tree/0750136f59dfcd0594d20d7dcc548b3f71b25e4b) combines a browser UI with a native/headless Rust service, local UDP and SQLite. Its dashboard has a useful history explorer, records, wind rose, rain comparisons, same-date-last-year views and exports. Browser-only deployment is different: a browser cannot receive the station's LAN UDP, so that mode depends on cloud APIs.

The [store](https://github.com/d4vid87/stormdesk/blob/0750136f59dfcd0594d20d7dcc548b3f71b25e4b/src-tauri/src/store.rs) retains selected values from the first `obs_st` row. The [server](https://github.com/d4vid87/stormdesk/blob/0750136f59dfcd0594d20d7dcc548b3f71b25e4b/src-tauri/src/server.rs) also maintains current packets and streams updates, but those do not make all rapid wind, lightning, status, extra rows and raw bytes into durable history.

There is a source/documentation discrepancy worth preserving in a fork evaluation: current stable source has paired-editor authorization for administrative operations, while some documentation still describes a trusted-LAN model. The implementation also has loopback trust and a legacy-LAN mode. A public reverse proxy must be designed around actual route authorization; neither “no authentication” nor “ready to expose wholesale” is accurate.

The [parallax WeatherDesk repository](https://github.com/parallaxintelligencepartnership/weatherdesk/tree/e96a7687febb0d953f3f4e4dcc78d8b46758c0e0) is an older August fork, not the current stable upstream. **Verdict:** prefer current upstream as a source of selected MIT UI modules. Port its useful views to our API rather than importing a second SQLite appliance and its external-data paths.

### AviationWX: especially relevant public presentation and freshness design

[AviationWX](https://github.com/alexwitherspoon/aviationwx/tree/42eaf02b7cd311bc577b6f5771e0c6f9b6972c39) brings current weather and multiple cameras together on public responsive pages. It supports static-image, stream and push-upload sources, background image caching, stale-data handling, historical image playback, kiosk mode and embeddable camera/weather widgets. Those are useful examples for the public experience at `weather.vallery.net` and an optional card on `vallery.net`.

Its [architecture](https://github.com/alexwitherspoon/aviationwx/blob/42eaf02b7cd311bc577b6f5771e0c6f9b6972c39/docs/ARCHITECTURE.md) is PHP with background worker processes and filesystem caches. It explicitly distinguishes observation times from fetch times, avoids treating unchanged images as newly captured, and uses atomic image promotion and source backoff. Those choices reinforce our freshness and provenance requirements.

Its [Tempest adapter](https://github.com/alexwitherspoon/aviationwx/blob/42eaf02b7cd311bc577b6f5771e0c6f9b6972c39/lib/weather/adapter/tempest-v1.php) uses WeatherFlow's REST API. Its [weather history](https://github.com/alexwitherspoon/aviationwx/blob/42eaf02b7cd311bc577b6f5771e0c6f9b6972c39/lib/weather/history.php) is a bounded JSON cache, with a 1,500-observation safety cap, rather than the required indefinite CNPG archive. Its [camera history](https://github.com/alexwitherspoon/aviationwx/blob/42eaf02b7cd311bc577b6f5771e0c6f9b6972c39/lib/webcam-history.php) is retained timestamped imagery, not our rendered native daily media pipeline. The [bridge](https://github.com/alexwitherspoon/aviationwx.org-bridge) provides interesting local acquisition/buffering patterns, but its outbound-upload role differs from our camera-only collector.

**Verdict:** a strong public UI and reliability reference. Replacing its aviation, API-provider and cache model would be a broad rewrite; use its interaction patterns and small well-bounded helpers where useful.

### Smaller libraries and other candidates screened

| Candidate | Finding and reuse boundary |
| --- | --- |
| [pyweatherflowudp](https://github.com/natekspencer/pyweatherflowudp/tree/c89fa2aeade2f8d834aaabce3bd2cffa398012d6) — MIT, 1.7.0 on 2026-10-08 | A useful local Python receiver/parser with units and derived quantities through Pint/PsychroLib. Its stateful event API emits an observation after updating from rows; it is not a complete receipt ledger. Persist original bytes and all rows before relying on current-state events. |
| [WeatherFlow Collector](https://github.com/lux4rd0/weatherflow-collector/tree/f4a0a72234caf34123734f2136a5945cf1489eef) — AGPL-3.0 | Local UDP and detailed Grafana views, but the examined startup requires cloud token/station metadata and InfluxDB. Its optional JSON output reserializes packets. Useful operational dashboard ideas; a poor canonical CNPG/local-only backbone. |
| [Snapshotr](https://github.com/hk21x/snapshotr/tree/1da5fc0257d8ca2fb76cfa47c30c294553cc05ac) | Multi-camera still gallery/date browsing/ZIP exports. Its inspected capture is minutes-scale RTSP/keyframe extraction, and no complete MP4 render path was found. **README says MIT, actual LICENSE is GPLv3**; resolve before importing code. |
| [Timelapse-Creator](https://github.com/ryang3d/Timelapse-Creator/tree/2d9ba25a661a3fff8a27d0fa056af500fafdea25) — MIT | Useful session/import/gallery controls. The inspected MP4 path hardcodes CUDA/NVENC, and its deployment has broad device/privileged assumptions. Auth, S3 and cron-like scheduling are roadmap items. GitHub main is from November 2025; another named Git host means that alone does not establish project-wide inactivity. |
| [Chronolapse](https://github.com/collingreen/chronolapse) — MIT | Desktop capture/assembly workflow with an older desktop-oriented stack. It adds little toward the k3s service boundary. |
| [Timelapse Webcam](https://github.com/bavamont/timelapse-webcam) — EUPL-1.2 | Electron/FFmpeg/Sharp desktop visual-monitoring application. Relevant to capture UX, but not the server/weather/history foundation. |
| [GlobalEmergency/webcam-archives](https://github.com/GlobalEmergency/webcam-archives/tree/c3ea8bc0f0866604c2a6f92144f8ca525ee87f7a) | Next.js/PostgreSQL/S3 camera archive with optional Google Cloud Vision timestamp OCR. No repository license was found in the reviewed tree and GitHub license metadata was null. Treat it as a public-source comparison, **not an established open-source reuse candidate**. Its database and storage choices alone do not provide our media or Tempest contracts. |

The smaller tools reinforce a useful separation: acquisition, observed-time metadata, rendering, publication, and public navigation should have independent contracts. Combining two complete applications would otherwise introduce overlapping schedulers, databases, retention rules and administrative surfaces.

### Frigate: strong evidence navigation, an object-centered model

[Frigate v0.18.2](https://github.com/blakeblackshear/frigate/releases/tag/v0.18.2), released October 15, 2026, is actively maintained and [MIT licensed](https://github.com/blakeblackshear/frigate/blob/v0.18.2/LICENSE). Its Explore and recording-history interfaces provide a useful interaction: find an event, inspect its evidence, then seek the corresponding timeline.

Its [semantic search](https://docs.frigate.video/configuration/semantic_search/) indexes tracked-object images and descriptions. Weather exploration needs time-window records even when the scene contains no tracked objects. [Review GenAI](https://docs.frigate.video/configuration/genai/genai_review/) uses selected preview or recording-derived images, with a documented ceiling of 20 frames. Security-oriented suspicion and danger assessments are not weather classifications, and sampled-image requests do not qualify native completed-video input. Local providers are supported, but model compatibility and resource sharing still require testing.

**Fork verdict:** selective reuse of search-result cards, filters, and timeline interactions is attractive. A full fork inherits capture, recording, object, and authentication assumptions that this application would largely replace. Keep the existing NVR independent; optional private deep links can be added without making public weather publication depend on it.

### Immich: a polished gallery, not a temporal video index

[Immich v3.3.5](https://github.com/immich-app/immich/releases/tag/v3.3.5), released October 21, 2026, offers mature calendar navigation, metadata filters, albums, external libraries, and video renditions. Its [AGPL-3.0 license](https://github.com/immich-app/immich/blob/v3.3.5/LICENSE) requires a deliberate licensing decision before copying code or maintaining a fork.

The stable implementation reveals a critical limitation for this use case: the [asset query](https://github.com/immich-app/immich/blob/v3.3.5/server/src/repositories/asset-job.repository.ts) selects `AssetFileType.Preview`, and the [Smart Search service](https://github.com/immich-app/immich/blob/v3.3.5/server/src/services/smart-info.service.ts) passes that image to the embedding model. This represents an asset's appearance through its preview; it does not establish understanding of events throughout a daily film. Its CLIP/VectorChord search is also a different model and index contract from the required description embeddings.

Local processing is possible, but [default map styles](https://docs.immich.app/guides/custom-map-styles/) reference an external provider, and model provisioning requires explicit handling. Documented external libraries ingest filesystem assets; they do not establish the required Garage integration.

**Fork verdict:** use gallery and rendition-lifecycle ideas, or offer optional private export of finalized videos. A full fork adds a second asset, job, authentication, and vector system while leaving the core weather and temporal-analysis requirements unresolved.

### CMU Time Machine and BreatheCam: exploration worth borrowing

The [Time Machine viewer](https://github.com/CMU-CREATE-Lab/timemachine-viewer/tree/d34f55968072a0b224dbfcfc04dfbd8d88e49891) supports spatially tiled timelapse navigation and tours, making it relevant to panoramic, multi-year exploration. Its permissive [CMU license](https://github.com/CMU-CREATE-Lab/timemachine-viewer/blob/d34f55968072a0b224dbfcfc04dfbd8d88e49891/LICENSE) allows evaluation and reuse subject to its conditions. Maintenance is slower: the viewer's latest published release is v1.0 from 2020, although its repository was pushed in March 2025. The creator's [build instructions](https://github.com/CMU-CREATE-Lab/timemachine-creator/blob/0d19521d7b6adfa41dd756db48ed5500db31a3eb/README.txt) describe a legacy toolchain, so modern compatibility remains unproven.

BreatheCam supplies an especially relevant [implemented interaction](https://github.com/CMU-CREATE-Lab/breathecam/blob/f6a83dd33eef9e74ec7c7696496d336bfd239b8e/breathecam.cmucreatelab.org/www/app/views/embeds/index.html.erb): clicking a telemetry point seeks or loads the corresponding capture, while playback moves the chart cursor. Its code uses external ESDR services; the application license was not established in this review.

**Fork verdict:** independently implement the shared UTC cursor and overview-to-detail navigation first. Consider a bounded Time Machine viewer prototype only if spatial zoom justifies tiled-media production and storage. Do not copy BreatheCam application code until its licensing is resolved, or inherit its endpoint and timestamp assumptions.


## 4. Where the existing requirements remain distinctive

The following are implementation contracts, not simply features that can be checked off because another project uses FFmpeg, PostgreSQL, or an S3 client.

| Requirement group | What the review established | Consequence for a fork |
| --- | --- | --- |
| Native timed acquisition — R03, R06, R08 | Several products accept HTTP images. Some invoke FFmpeg per capture, impose multi-second scheduler limits, or tie capture to a general web/admin process. | Prove actual camera cadence, preserve native snapshot bytes and timestamps, and isolate camera network access. A configurable interval alone does not meet the contract. |
| Civil partitions and time mapping — R04, R05 | All-sky software has solar day/night transitions; other tools offer fixed daily windows or sunrise-event clips. | Explicitly implement half-open civil-dawn/dusk intervals, daylight-saving behavior, the combined 7,200-frame budget, and the UTC-to-playback map. Similar solar settings are useful starting points, not equivalence. |
| Smoothness and fidelity — R09–R11 | FFmpeg image-sequence rendering, deflicker, stacking, and hardware encoding are common. | None of the inspected pipelines established our interpolation-before-shutter-integration contract with real acquisition timing. GPU encoding does not establish optical-flow acceleration. Keep the quality/throughput pilot. |
| All received telemetry — R13–R15 | Local UDP listeners exist, including usable libraries and dashboards. Their usual product is normalized current conditions or selected observations. | Preserve original datagrams before parsing; retain every observation row, status/event packet, unknown field, receipt provenance, and replay identity. A dashboard with wind and rain is not a complete acquisition archive. |
| CNPG and HA — R14, R16, R19 | Several systems use SQLite; Stormglass can write PostgreSQL; Immich uses PostgreSQL for its own asset model. | Verify every API read path, failover, migrations, durable jobs/spool, and recoverability. An optional PostgreSQL sink does not eliminate another authoritative database. |
| Garage and delivery — R17–R19, R23 | Some tools upload to S3-compatible storage. | Qualify Garage operations, immutable publication, same-origin ranges/downloads, shared-bucket authorization, database backups/WAL, B2 custody, and coordinated recovery. None of those follows from an S3 checkbox. |
| Weather video AI and search — R20–R22 | Image captioning, object-description embeddings, and CLIP image search are available elsewhere. | Qualify completed-video analysis separately; retain weather evidence and temporal provenance. Metadata embeddings and visual embeddings solve different retrieval problems. |
| Fleet deployment — R02, R24–R28 | Many projects offer Compose or a single appliance; a few provide Kubernetes examples. | Local builds, independent ownership, restricted network paths, GPU co-tenancy, existing-service preservation, and the cluster's backup patterns still require phase-1 evidence. |

## 5. Features worth adding or explicitly reserving

**Decision status:** these are the original proposals. The owner selected all-three-camera keograms and delayed previews, hourly retained originals, per-camera linked plots, bookmarks/feed, almanac, measured filters, private editorial controls and an embed. Text embeddings come first; the east/west composite and visual-index investigation are explicitly later work. Use the accepted decision record and v4 requirements for implementation scope.

These are proposals for this project. The upstream inspirations are described in section 3; the implementation recommendations here are engineering judgments.

### 5.1 A visual index of each day: keograms

A keogram compresses a sequence of sky images into one time-indexed image: take a narrow vertical strip from observed captures, then place the strips on a fixed elapsed-time grid. It can make changing cloud, light, and clear-sky periods easier to find before playing a video. Allsky and indi-allsky provide useful examples.

For our panoramic views, configure the strip or region separately per camera. Preserve a calibrated relationship between the horizontal axis and real UTC time, show missing intervals as gaps, and label the selected sky region. Generate one keogram per published civil segment. A combined cycle view may join the two with the dusk boundary marked. Clicking a column resolves its UTC instant to the correct daytime or overnight video and then to that video’s playback map. Do not present exposure changes or camera infrared transitions as physical weather measurements. Generate it before snapshot deletion, publish it as an immutable artifact, and include its identity in the sidecar and recovery manifest.

**Recommendation:** add daytime and overnight keograms for each sky camera. A front-yard version is optional because foreground changes may dominate its usefulness. A keolapse, which animates a developing keogram, can wait.

### 5.2 Seasonal comparisons without retaining the full capture archive

ReoLapse's yearly rendering and ChronoSnap's long-running capture jobs suggest a valuable second time scale: changes across weeks, seasons, and years. Daily compressed videos are not ideal substitutes for deliberately selected comparable source images.

One option is to retain **three selected native stills per camera per day**, anchored to defined solar phases, as explicitly private retained originals. Another is one still per hour. Public seasonal comparisons would use separately generated images at native geometry with the approved privacy masking and metadata sanitization; exact originals would remain inaccessible through public routes. This would be a deliberate exception to purging every source snapshot, not a hidden increase in staging retention. Private originals need their own approved retention, authorization and backup policy. Public derivatives follow the same publication, withdrawal and recovery controls as videos. The remaining acquisition frames still follow the existing purge contract.

For illustration only, at an assumed 2 MB per selected image, three cameras × three images/day × 365 days is about **6.57 GB/year** before replication, backup, variants, and metadata. Hourly selection is about **52.56 GB/year** on the same assumptions. Actual snapshot sizes must be measured. Missing or obscured samples remain missing; the system must not synthesize an apparently observed seasonal frame.

**Recommendation:** preserve a small explicit seasonal set if annual comparisons are wanted, with a proposed five-year horizon independent of video retention. The owner may choose a different horizon; imagery, derived products and metadata need a coherent policy even after a related daily video expires. Make camera framing/calibration changes versioned so comparisons can explain discontinuities. This proposal does not shorten the baseline's retained weather-observation history.

### 5.3 One explorer for video, cameras, and measured weather

The strongest interface improvement is a shared real-time cursor. When a viewer seeks through a timelapse, show the original local time and align temperature, precipitation, wind/gust, illuminance, and lightning-event plots. Selecting a sensor event should seek the video. On larger screens, optionally synchronize east, west, and front-yard views by source time; mobile can keep one video active.

This builds on the baseline's required time map and seekable search. It does not imply the cameras were exposed at exactly the same instant. Show acquisition uncertainty and gaps when they matter; do not manufacture values between sparse measurements. Do not require three native-resolution decoders to run on a phone.

**Recommendation:** prioritize linked video/weather navigation. Add simultaneous three-camera playback only after testing its mobile/desktop resource cost.

### 5.4 Weather events as navigable objects

Create a first-class event record for rain onset, a measured gust peak, a lightning report, a rapid temperature change, or an AI-observed cloud transition. Keep sensor-derived, visual, and human annotations distinct. Events can support a daily event strip, bookmarks, search results, and shareable links.

Start with bookmarks into the required day/night videos. Optional dedicated event videos would be **additional** media, outside the baseline daily pair's two-minute budget. To preserve more event detail, generate those clips from the original staged snapshots before cleanup, with an explicit raw-frame lease, bounded deadline, and failure policy. A timely detector must acquire that lease before the cleanup barrier. Late findings from asynchronous AI use bookmarks or extracts of retained daily video; they must not reopen or indefinitely extend raw retention. A clip made later from the compressed daily video cannot recover deleted temporal detail. A Tempest lightning report does not prove the flash was visible in a camera frame, and one-second snapshots may miss it entirely.

**Recommendation:** event bookmarks first; separate event renders only if the owner values them enough to expand processing and retention scope.

### 5.5 An almanac and quantitative search

The planned database makes useful views possible without adding another observation source: daily/monthly/yearly summaries, precipitation calendars, wind roses, temperature records, and comparisons with earlier observed periods. Local sun/moon context can accompany those views. Growing-degree calculations, frost windows, and other derived metrics can be added with documented formulas and required inputs. A short local record must not be labeled a long-term climatological normal. Existing history-query and export requirements remain in every option; CSV is only a proposed additional export format.

Search should combine exact filters with semantic ranking. For example: “dramatic clouds” plus a selected camera/date range and **measured gust above a chosen threshold**. SQL should enforce the numeric and temporal conditions; embeddings should rank the visual/narrative concept. Weather measurements need an explicit relationship to the clip's source interval and coverage. Do not ask an LLM to infer a numeric threshold from a picturesque description.

**Recommendation:** include a modest local almanac and numeric weather filters. Keep formulas and units versioned, and expose data coverage beside aggregates.

### 5.6 Visual search as a separate, optional index

Frigate and Immich demonstrate why searching images can find things that generated captions omit. Our required text embeddings are a sound baseline for narrated weather. An optional second index over representative observed frames could support “find skies that look like this” or retrieval of an unmentioned rainbow/cloud formation.

That requires a separately qualified local vision embedding model, a distinct embedding space and generation, image selection and retention rules, GPU/CPU budgeting, relevance fixtures, and a strategy for combining image and text results. Existing text vectors cannot be compared directly with an unrelated visual model's vectors. Images need exact source-time links, and generated/interpolated frames should not silently become observational ground truth. Private retained originals and approved public preview derivatives must remain separate identities; an embedding job must not make an original publicly addressable.

**Recommendation:** retain a schema extension point and defer the second model until a baseline evaluation shows a useful retrieval gain.

### 5.7 Publication surfaces and editorial controls

AviationWX provides good examples of weather/camera widgets, kiosk presentation, and current-image freshness. ChronoSnap and media galleries suggest favorites, comparisons, and mobile installation. These are useful optional surfaces for `vallery.net` or a wall display.

Publishing current camera images is an additional product decision. A delayed current image should have a clear capture time and delay; it should be produced by the publication path, never by making the public website a proxy to a camera. A static latest image does not require a public live stream.

Add a private correction workflow for AI descriptions, event labels, and selected posters if the owner wants editorial control. Keep original analysis, author/reason/time of corrections, and regenerated search documents. Public automated publication can continue while descriptions are clearly labeled and correctable.

**Recommendation:** a small embeddable weather card and correction capability offer good value. Kiosk/PWA/offline behavior can be added after the core archive; cached conditions must always retain their observation time and stale state.

## 6. Proposed design changes, subject to answers

**Historical proposal table:** the answers are now recorded. The concrete FK, hourly/DST identity, original/public-derivative separation, preview embargo, cleanup, editorial and recovery contracts are normative in requirements v4.0. This table explains their research origin rather than leaving them undecided.

The review supports making the **observed time interval** the common link among videos, telemetry, events, annotations, and search documents. It does not require replacing CNPG, Garage, NFS staging, or the service boundaries already specified.

| Proposed addition | Data/contract change | Effect on existing work |
| --- | --- | --- |
| Keograms | New artifact type, camera region configuration, UTC axis map, gap metadata | Add derived-media generation before cleanup; preserve same publication/backup controls. |
| Seasonal selected frames | Explicit private-original retention plus approved public derivatives, source time and framing version | Owner-approved exception to total snapshot purge; capacity and backup growth become measurable. |
| Linked explorer | Public source-time cursor and bounded aggregate/event APIs | Reuse the baseline time map; additional frontend synchronization and accessibility work. |
| Weather events | Typed event table, evidence links, source intervals, provenance, confidence/quality state | Powers bookmarks and numeric filters; can later drive extra clips and notifications. |
| Editorial correction | Versioned human overrides with audit records and embedding invalidation | Preserve automated analysis; update published metadata transactionally through the existing outbox. |
| Visual embedding index | Separate model profile, selected-frame documents, independent index generation | Adds an optional inference workload; does not replace required text search. |
| Embeds/current previews | Narrow public routes, capture timestamps, cache rules, optional publication delay | Keep media behind the weather endpoint and capture pods isolated. |

These should be separate issues with dependencies on the existing capture, metadata, publication, API, and recovery work. A feature should not become an upstream dependency of ordinary daily publication unless it is essential to that publication's correctness. Extra rendering and AI failures should degrade their own artifacts, not block unrelated cameras.

## 7. Questions for the owner

All questions have been answered. Original options/recommendations are preserved below; the **accepted answer** column and linked decision record control. Later work is explicitly tracked and excluded from the initial rollout closure. The owner expanded Q02 to all three cameras and selected Q04=B with a separate combined east/west composite follow-on.

| ID | Decision and choices | Original recommendation and consequence | Accepted answer |
| --- | --- | --- | --- |
| **Q01** | **How aggressively should we pursue a fork?** A: independent Weather application with selective component reuse. B: first run bounded Glimpser and Stormglass adaptation experiments, then choose. C: prioritize one named upstream as the foundation and explicitly revisit incompatible requirements. | **A**, or **B** if reducing uncertainty about implementation effort is worth a phase-1 experiment. Do not assume a fork saves work until the changed subsystems and retained code are demonstrated. | **A:** independent Weather; reference reviewed projects where appropriate. |
| **Q02** | **Add a visual daily sky index?** A: daytime and overnight keograms for both sky cameras in the initial release. B: keograms later. C: video/poster archive only. | **A.** Useful navigation with modest derived storage. A front-yard keogram and animated keolapse can remain optional. | **Yes, all three cameras:** daytime and overnight keograms, including frontyard_1. |
| **Q03** | **Keep a small set of images for seasonal/yearly comparisons?** A: three private native originals per camera/day at defined solar phases, with sanitized public comparison derivatives. B: hourly private originals and the same public-derivative boundary. C: no extra retained source stills. For A/B, proposed retention is five years; specify another horizon if preferred. | **A.** Requires an explicit sparse-retention exception. Approximate original-image payload growth at 2 MB/image: A = 6.57 GB/year; B = 52.56 GB/year, before derivatives, replicas/backups. | **B:** hourly private native originals in Garage, CNPG indexing and enforced day/camera FKs; five-year offered default. |
| **Q04** | **How integrated should historical exploration be?** A: video linked to weather plots, with optional synchronized multi-camera desktop mode. B: linked video/weather only. C: independent video and weather pages. | **B initially**, with A as a later enhancement. The shared UTC time contract should support either. | **B initially:** linked plots by camera. Add accepted later **single east/west composite skycam video** (WX-45). |
| **Q05** | **How should interesting weather events appear?** A: bookmarks and a daily event strip inside the normal videos. B: A plus separate, slower event videos rendered from staged snapshots. C: descriptions/search only. | **A.** B expands media, render budgets, and raw-frame leases; it must not silently change the 120-second daily-pair contract. | **A:** bookmarks/daily event strip, no extra event render pipeline. |
| **Q06** | **Publish current camera previews as well as completed timelapses?** A: sky cameras only, refreshed about once/minute with an explicit 10-minute publication delay. B: all three cameras on the same delayed schedule. C: completed timelapses/posters only. | **C unless current views are a desired product feature.** A is the smaller expansion; cadence and delay are configurable policy, not camera-access changes. | **B:** all three cameras; about one-minute refresh and minimum ten-minute public delay. |
| **Q07** | **How much local weather history UI should we build?** A: records, rain calendar, wind rose, sun/moon context, and daily/monthly/yearly comparisons. B: A plus gardening/frost/growing-degree views and CSV as an extra export format. C: baseline charts, query API and required history exports. | **A.** Baseline exports remain in all choices. B is useful for gardening or other analysis; derived formulas and coverage need explicit contracts. | **A:** local records/rain calendar/wind rose/sun-moon context/comparisons; retain required exports. |
| **Q08** | **Add measured-weather filters to search?** A: combine camera/date/text search with rain, gust, temperature and other numeric conditions. B: keep the baseline text/camera/date search first. | **A**, starting with a small typed filter set. This is reliable filtering over telemetry, not LLM-generated measurements. | **A:** typed measured-weather filters. |
| **Q09** | **Should search understand visual appearance directly?** A: text embeddings first; reserve a visual-index extension. B: include local image embeddings and image-similarity search in the initial scope. C: text search only, with no planned image index. | **A.** B adds a model, image retention, relevance testing, and GPU scheduling work. | **A:** text first; reserve visual-index research (WX-46). |
| **Q10** | **How much control should you have over AI descriptions?** A: automatic labeled publication plus private corrections, tags, favorites, and poster selection. B: review AI text before it becomes public, while videos publish automatically. C: fully automatic descriptions without an editor. | **A.** Keep revisions/provenance and reindex corrections. B adds a pending-review queue but should not hold videos back. | **A:** automatic labeled AI plus private corrections/tags/favorites/poster choices. |
| **Q11** | **Should weather events notify anything?** A: in-site event feed only. B: add a narrowly configured webhook to an existing local notification service. C: add user subscriptions/email/mobile push. | **A initially.** B can be a later integration; C creates a separate account/subscription product. This question authorizes design choices, not sending any current message. | **A:** in-site event feed only. |
| **Q12** | **Is optional external context desirable?** A: retain exclusively local observations and astronomical calculations. B: optional server-fetched/cached radar and official alerts. C: B plus forecasts. | **A under the current no-external-dependency requirement.** B/C require an explicit amendment: the core still works offline, but fresh context depends on providers, attribution, terms, and expiry rules. | **A:** local observations and local astronomical calculations only. |
| **Q13** | **Where else should the UI appear?** A: responsive website plus a small embeddable card for `vallery.net`. B: A plus kiosk and installable/offline PWA modes. C: website only. | **A.** B should follow the first release unless a wall display is a primary use case. | **A:** responsive site and vallery.net weather card. |
| **Q14** | **How portable should the open-source product be?** A: a configurable single-site application with camera adapters and installation documentation. B: optimize for this fleet and document customization. C: a multi-site, multi-user hosted platform. | **A.** Public portability is useful; C substantially expands identity, tenancy, permissions, and operations beyond the current project. | **B:** fleet-first, document customization. |
| **Q15** | **What should govern upstream code reuse?** A: prefer permissively licensed components; consider other licenses case by case. B: accept a copyleft foundation if it materially improves the product. C: have Codex propose the project license and reuse policy during phase 1. | **C**, with the owner's final license choice recorded before code imports. A README badge alone is insufficient; check actual files and bundled assets/models. | **C:** Codex proposes license/reuse policy; owner's final choice before imports. |
| **Q16** | **How live should current conditions feel?** A: ordinary current conditions refreshed about every 15 seconds. B: rapid-wind updates near the station's roughly three-second cadence in every active view. C: fast updates only in a dedicated instrument/kiosk view. | **A initially**, or C if a wall display is important. Every option still archives all received telemetry; this choice affects public update delivery and frontend/API load. | **A:** approximately 15-second current conditions; retain all received telemetry. |

## 8. Codex follow-through for the selected direction

Implement the independent, fleet-first direction. Keep the original requirement and issue identities stable, use the [accepted decision record](../decisions/2026-10-23-product-scope.md), and follow the [updated first assignment](../archive/planning-2026-10/codex-first-task.md). The 46-key candidate graph distinguishes required initial work, conditional GPU/CDN paths and two later items; none of those keys is a fabricated GitHub issue number.

1. **Resolve implementation details, not the answered product questionnaire.** Investigate per-camera keogram regions, hourly selection/actual calendar days, foreign keys, privacy processing, delay enforcement, bounded source-input promotion, framework/auth and local almanac/search contracts. Preserve all three cameras and the exact civil/day-pair time model.
2. **Use focused references.** Requirements §20.1 maps Allsky/indi-allsky to image products, ReoLapse to archival/publication ideas, ChronoSnap to controls, pyweatherflowudp/Stormglass to local telemetry, WeeWX/Belchertown/StormDesk to weather history, AviationWX to freshness/embeds, BreatheCam to linked exploration, and Frigate/Immich/Glimpser to evidence/gallery/editorial ideas. The source ledger below controls revision attribution. No application fork experiment is a Phase-1 prerequisite.
3. **Resolve license/reuse policy concretely.** Codex proposes the project license and acceptable reuse policy, examines actual file/dependency/asset/model notices, and records the owner's final choice before imports. Ideas from unlicensed or disputed candidates do not grant code-copying rights. Continue other research while a specific license decision is pending.
4. **Extend the hard boundaries.** Raw originals remain private, even after sparse Garage retention. Every retained still has real day/camera/hour relationships; video expiry does not remove it. Ten-minute preview eligibility applies at the origin and to newly archived still derivatives. Source-dependent keograms/hourly selections precede dense raw cleanup; AI remains asynchronous.
5. **File selected work and deferred work explicitly.** WX-36–WX-44 cover the new initial products. WX-45 is the accepted combined east/west rendered skycam product. WX-46 is reserved visual-index research, not permission to deploy a second model. Neither later item may block WX-35's initial-release closure.
6. **Preserve the original research/rollout stop rule.** Read-only inventory and approved bounded qualification can select an implementable design. A missing production component becomes a mandatory implementation/qualification issue rather than an incidental shared-service change. File the actual issues and native dependencies, verify them by readback, and report implementation separately from research completion.

GPU co-tenancy, Tempest broadcast reachability, NFS durability/fencing, Garage authorization and backup compatibility, exact video-route behavior, CNPG/extensions and local build/CI integration still require live-estate investigation. The owner choices do not imply those capabilities have been proved or resources deployed. Full generic portability, external weather context, outbound product notifications, additional event clips, kiosk/PWA and simultaneous multi-player views are not added to initial scope.

## 9. Evidence and revision ledger

Source links beside the findings identify the inspected implementation. The following ledger distinguishes source revisions from release labels so future Codex work can reproduce or update the assessment. All repository/API/document reads were performed on 2026-10-23. Main-branch code and an independently tagged release can differ; the per-claim link controls which was inspected.

| Repository | Source revision or release used for code assessment |
| --- | --- |
| `aaronwmorris/indi-allsky` | [`18b8407d`](https://github.com/aaronwmorris/indi-allsky/tree/18b8407d31843f3df9c69f8b5b16443f7df65c82), default-branch commit 2026-10-19; [packaged release](https://github.com/aaronwmorris/indi-allsky/releases/tag/indi_v2026.09.01). |
| `AllskyTeam/allsky` | [`2b2c7b13`](https://github.com/AllskyTeam/allsky/tree/2b2c7b1347d076a489df5e614acf27de450738fb), commit 2026-10-06; keogram also inspected at [v2026.10.01](https://github.com/AllskyTeam/allsky/releases/tag/v2026.10.01). |
| `SeriesOfTubez/reolapse` | [`2ae0589f`](https://github.com/SeriesOfTubez/reolapse/tree/2ae0589f7449296340389ee6abfd0c15228513b8), commit 2026-10-13; [v0.5.1](https://github.com/SeriesOfTubez/reolapse/releases/tag/v0.5.1). |
| `kernelkaribou/chronosnap` | [`01b055cb`](https://github.com/kernelkaribou/chronosnap/tree/01b055cb102ce60d226ce9e0b8ba93ce102aae73), commit 2026-09-10; [v3.8.0](https://github.com/kernelkaribou/chronosnap/releases/tag/v3.8.0). |
| `KristopherKubicki/glimpser` | [`9a62aba1`](https://github.com/KristopherKubicki/glimpser/tree/9a62aba11f6aff2858e2794b1e9ff9ca4047e7a9), reviewed main; [v0.2.10](https://github.com/KristopherKubicki/glimpser/releases/tag/v0.2.10). |
| `jacaudi/stormglass` | [`ffa4b40f`](https://github.com/jacaudi/stormglass/tree/ffa4b40f2472f8248660b4386910131128121ec5), commit 2026-10-17; [v1.1.1](https://github.com/jacaudi/stormglass/releases/tag/v1.1.1). |
| `weewx/weewx` | [`a5790be2`](https://github.com/weewx/weewx/tree/a5790be2d0d33da50440eb37e0a9e21499246920), commit 2026-10-22; [v5.5.2](https://github.com/weewx/weewx/releases/tag/v5.5.2); official 5.5 documentation. |
| `poblabs/weewx-belchertown` | [`049f6159`](https://github.com/poblabs/weewx-belchertown/tree/049f615952da8d9e0c779ccab9e6f64cf88fedf6), commit 2026-10-08; [3.0.0](https://github.com/poblabs/weewx-belchertown/releases/tag/weewx-belchertown-3.0.0). |
| `captain-coredump/weatherflow-udp` | [`eedfaf7b`](https://github.com/captain-coredump/weatherflow-udp/tree/eedfaf7b1ee1d0eb51513966b7a09e7622858791), commit 2024-02-19. |
| `d4vid87/stormdesk` | [`0750136f`](https://github.com/d4vid87/stormdesk/tree/0750136f59dfcd0594d20d7dcc548b3f71b25e4b), stable/default line 4.5.2; separate 5.0 preview releases are not treated as this source. |
| `parallaxintelligencepartnership/weatherdesk` | [`e96a7687`](https://github.com/parallaxintelligencepartnership/weatherdesk/tree/e96a7687febb0d953f3f4e4dcc78d8b46758c0e0), older August fork. |
| `natekspencer/pyweatherflowudp` | [`c89fa2ae`](https://github.com/natekspencer/pyweatherflowudp/tree/c89fa2aeade2f8d834aaabce3bd2cffa398012d6), reviewed main; [release history](https://github.com/natekspencer/pyweatherflowudp/releases). |
| `lux4rd0/weatherflow-collector` | [`f4a0a722`](https://github.com/lux4rd0/weatherflow-collector/tree/f4a0a72234caf34123734f2136a5945cf1489eef); last published release observed was 5.1.55 in July 2024. |
| `alexwitherspoon/aviationwx` | [`42eaf02b`](https://github.com/alexwitherspoon/aviationwx/tree/42eaf02b7cd311bc577b6f5771e0c6f9b6972c39), commit 2026-10-23. Companion bridge was a documentation-level screen. |
| `blakeblackshear/frigate` | [v0.18.2](https://github.com/blakeblackshear/frigate/releases/tag/v0.18.2) plus current official semantic-search, Explore, and GenAI documentation. |
| `immich-app/immich` | [v3.3.5](https://github.com/immich-app/immich/releases/tag/v3.3.5), including Smart Search and asset-query source, plus current official documentation. |
| `CMU-CREATE-Lab/timemachine-viewer` | [`d34f5596`](https://github.com/CMU-CREATE-Lab/timemachine-viewer/tree/d34f55968072a0b224dbfcfc04dfbd8d88e49891). |
| `CMU-CREATE-Lab/timemachine-creator` | [`0d19521d`](https://github.com/CMU-CREATE-Lab/timemachine-creator/tree/0d19521d7b6adfa41dd756db48ed5500db31a3eb). |
| `CMU-CREATE-Lab/breathecam` | [`f6a83dd3`](https://github.com/CMU-CREATE-Lab/breathecam/tree/f6a83dd33eef9e74ec7c7696496d336bfd239b8e); interaction code inspected, application license not established. |
| `hk21x/snapshotr` | [`1da5fc02`](https://github.com/hk21x/snapshotr/tree/1da5fc0257d8ca2fb76cfa47c30c294553cc05ac), commit 2026-07-21; actual LICENSE and conflicting README both reviewed. |
| `ryang3d/Timelapse-Creator` | [`2d9ba25a`](https://github.com/ryang3d/Timelapse-Creator/tree/2d9ba25a661a3fff8a27d0fa056af500fafdea25), commit 2025-11-18. |
| `GlobalEmergency/webcam-archives` | [`c3ea8bc0`](https://github.com/GlobalEmergency/webcam-archives/tree/c3ea8bc0f0866604c2a6f92144f8ca525ee87f7a), default-branch commit 2026-04-12; repository push July 2026. No license found. |

The secondary desktop screens used their linked project documentation; they were not given the same source-level qualification as the leading candidates. No ranking is based on star counts, promotional claims about test coverage, or an assumption that a recent release is production-ready.

For subsequent validation, use the official [Tempest UDP contract](https://apidocs.tempestwx.com/reference/tempest-udp-broadcast), [FFmpeg filter documentation](https://ffmpeg.org/ffmpeg-filters.html), and the versioned platform references already listed in the baseline. Upstream code reuse does not replace those contract tests.
