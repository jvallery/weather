# Public description text (C18)

Status: implemented by `app/weather/ai/render.py` (#23). Supersedes the WeatherNarrative synthesis candidate: no model-written prose is ever published, and there is no Stage C model call ([C18](../decisions/2026-10-24-takeover-replan.md)).

## Inputs

- **Accepted Stage A observations**, one structured reply per hour window ([visual-analysis.schema.json](../../app/weather/ai/contracts/visual-analysis.schema.json), v2): category, assessment (`visible`, `possible`, `not_observed`, `not_assessable`), confidence, optional intensity and sky cover, and limitation codes. Observation IDs are application IDs bound to the run.
- **The Stage B packet**: whole-segment station aggregates with packet-local IDs, values rendered in the canonical unit and the registry's fixed conversions by `evidence.py`, coverage, and the evidence hash (C5).

## Rendering

- One visual claim per (category, phrase, hedge), in scene order: "The sampled east-sky frames show broken clouds in all 3 reviewed hour windows." `possible`, and `visible` at low confidence, render as "possibly show". Phrases come from fixed tables (sky cover, intensity, category).
- Absence only where it is provable: "No rain was visible in the sampled … frames" when every reviewed window assessed that category as `not_observed`; a measured zero rain total only at full station coverage. A partial positive total says "over partial station coverage".
- Measured claims print station values only: temperature range, mean wind, strongest gust, rain total, lightning count when non-zero.
- Limitations are fixed sentences: unreviewed hours, no keogram, withheld station metrics, `not_assessable` categories per window count, lens and artifact categories, and the model's limitation codes.
- The summary is the claims joined; search window text is the same phrases per window. At most 16 claims and 8 limitations.
- `RENDER_REVISION` is hashed into every revision's prompt hash, so a template change is a new revision identity.

## States

`complete` when every available window was reviewed; `degraded` (`visual_partial`, `visual_invalid` or `call_budget`) when some were not but claims exist; `failed` (`no_evidence`) when neither imagery nor measurements yield a claim. A measured-evidence change re-renders from the stored observations (refresh `synthesis`) without calling the model.
