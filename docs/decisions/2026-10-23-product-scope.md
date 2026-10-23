# Product scope decisions — 2026-10-23

**Status:** owner decisions accepted; incorporated into [requirements v4.0](../requirements.md).  
**Source:** the owner's answers to Q01–Q16 in the [open-source landscape review](../research/open-source-landscape.md#7-questions-for-the-owner).  
**Implementation state:** requirements and candidate backlog only; Codex's first assignment remains investigation, decision records, repository configuration, and filing the real implementation issues with verified dependencies.

This record supersedes the research report's recommendations wherever the owner selected a different option. The application stays independent. The reviewed projects are implementation references; no application fork or mandatory fork experiment is selected. Existing cluster decisions D01–D16 remain research work, and the earlier camera, time, storage, privacy, telemetry, GPU, recovery and deployment contracts remain in force.

## Accepted choices and traceability

| Question | Accepted decision | Requirements / candidate work |
|---|---|---|
| Q01 | Independent Weather application; reference the researched solutions in the design where useful. Selective reuse must pass license and interface review. | R02, R24; D01; requirements §20.1. |
| Q02 | Daytime and overnight keograms for **all three cameras**, including `frontyard_1`. | R29; WX-36. |
| Q03 | **One selected native original per camera per actual elapsed local hour**, retained privately in Garage, indexed in CNPG with explicit camera and calendar-day foreign keys. Publish sanitized comparison derivatives. | R30; WX-37; §5.5, §6.10, §10.3. |
| Q04 | Per-camera video linked to weather plots in the initial release. Track a **follow-on single composite skycam video containing east and west views**. | R32 and R38; WX-39 and deferred WX-45. |
| Q05 | Event bookmarks and daily event strip within normal timelapses. Additional slower event videos are outside the initial scope. | R33; WX-40. |
| Q06 | Current camera previews for **all three cameras**, using the delayed schedule in the questionnaire. | R31; WX-38. |
| Q07 | Local records, precipitation calendar, wind rose, sun/moon context, and daily/monthly/yearly comparisons. Existing historical query/export requirements remain. | R34; WX-41; R14. |
| Q08 | Typed measured-weather filters combined with camera/date/text search. | R35; WX-42. |
| Q09 | Text embeddings first. Reserve a later visual-index extension; no initial image embedding model or image-similarity service. | R22; deferred research candidate WX-46. |
| Q10 | Automatically publish labeled AI descriptions. Provide private corrections, tags, favorites and poster selection, with provenance and reindexing. | R36; WX-43. |
| Q11 | In-site event feed only. No product webhook, subscription, email or push integration. | R33; WX-40. |
| Q12 | Local observations and local astronomical calculations only. No external radar, forecast or alert source. | R24; §1.3, §10.6. |
| Q13 | Responsive website plus a small embeddable weather card for `vallery.net`. Kiosk/offline/PWA modes are not initial requirements. | R37; WX-44. |
| Q14 | **Fleet-first** implementation with documented customization. No general multi-site or multi-tenant product requirement. | R02, R24; D01, D13; WX-34. |
| Q15 | Codex proposes the project license and reuse policy in Phase 1; record the owner's final choice before importing upstream code. | D01; §20.1. |
| Q16 | Current conditions refresh approximately **every 15 seconds**. Archive all received telemetry independently of the UI cadence. | R14, R15; WX-13, WX-20. |

## Selected defaults and interpretations

These details make the answers implementable without describing unmeasured properties as facts. They can be refined during commissioning only within the selected product/security contracts.

- **Hourly retention:** five years is the default offered with Q03=B. The owner explicitly selected hourly cadence, Garage storage and database/day relationships; they did not separately restate the horizon. Record this as the selected questionnaire default, independently configurable from video retention. No production deletion starts before D07/D15 record the lifecycle and recovery policy.
- **Delayed previews:** Q06's selected schedule is approximately one-minute refresh with a **minimum ten-minute age at first public release**. Use trusted source-time bounds, not request time or file modification time. Delay remains a publication policy, independent of the 15-second weather refresh. A capture-time uncertainty interval must be wholly old enough; unknown or implausible timing is ineligible. All three cameras follow the same policy.
- **Day relationships:** a still belongs to the local calendar day containing its actual source time. It also links to its source capture/segment and dawn-to-dawn solar cycle. These identities differ across midnight. The calendar day has 23, 24 or 25 actual local hourly slots around daylight-saving changes; repeated hour labels retain offset/fold identity. Selection never substitutes a different hour's frame for missing coverage.
- **Combined skycam:** the accepted follow-on is a single rendered composite with both sky cameras, not merely two synchronized players. Codex must specify layout, aspect ratio, alignment, missing-view behavior, resource cost and acceptance criteria. Start by evaluating labeled composition; do not assume the two views can be geometrically stitched into a panorama. Preserve the existing per-camera outputs and civil segmentation/duration contract unless a later owner decision changes them.
- **Private originals and public derivatives:** archived native JPEG bytes remain private. Public stills/previews use approved masking, metadata removal and same-origin delivery. The sparse hourly archive is an explicit exception to the raw-staging purge; it does not authorize retaining the continuous snapshot sequence.
- **Public AI, private control:** valid descriptions publish automatically with their provenance labels. The editor requires authenticated operator access. Preserve model output and human revision history separately; favorites are private by default, while explicitly published corrections/tags/poster choices create new public revisions. Automatic reruns must not overwrite an active human correction.
- **Deferred scope:** WX-45 is the accepted follow-on composite product. WX-46 records a future visual-index investigation, not approval to deploy an image embedding model. Neither blocks initial-release acceptance. Multi-camera synchronized desktop playback, additional event clips, external weather context, notifications, kiosk/PWA modes and multi-tenancy are not added by these answers.

## Consequences for implementation

The initial release adds six per-cycle keograms, sparse hourly stills and comparison browsing, delayed previews, linked video/telemetry plots, weather bookmarks and an in-site feed, the local almanac, measured search filters, private editorial controls and the main-site embed. These are requirements, not optional polish after the original website is complete. A failure of one derived product must be explicit without hiding otherwise valid videos or blocking unrelated cameras.

The database needs a real local-calendar-day identity in addition to `solar_cycle`, archive-hour slots with explicit missing states, still-to-day/camera foreign keys, immutable asset provenance and editorial/event records. The publication and cleanup protocols must account for hourly-original archival and keogram extraction before removing their only source inputs. Media/metadata authorization, backup closure and recovery-aware retention must include the new asset classes.

Codex must investigate current cluster capability rather than ask the owner to decide implementation details that can be established from evidence. In particular, the preview/keogram worker design, per-camera keogram region, hourly-selection tolerance, schema/partition strategy, public/private API authorization, almanac formulas and coverage, reusable components, GPU co-tenancy and layout for the later composite belong in the Phase-1 design records and the filed issues.

The [candidate graph](../implementation/backlog-candidate.json) preserves the original WX-01–WX-35 keys, adds WX-36–WX-46, and distinguishes initial work from conditional GPU/CDN work and deferred follow-ons. Codex must file those selected outcomes as real issues after research, including WX-45 and the reserved WX-46 investigation, and verify that deferred work cannot become an accidental prerequisite of the initial rollout.
