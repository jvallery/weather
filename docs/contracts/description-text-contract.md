# Description text contract (C18)

Status: implemented by `app/weather/ai/render.py` (#23). Supersedes the WeatherNarrative synthesis candidate: no model-written prose is ever published, and there is no Stage C model call ([C18](../decisions/2026-10-24-takeover-replan.md)).

## Inputs

- **Accepted Stage A observations**, one structured reply per hour window ([visual-analysis.schema.json](../../app/weather/ai/contracts/visual-analysis.schema.json), v2): category, assessment (`visible`, `possible`, `not_observed`, `not_assessable`), confidence, optional intensity and sky cover, and limitation codes. Observation IDs are application IDs bound to the run.
- **The Stage B packet**: whole-segment station aggregates with packet-local IDs, values rendered in the canonical unit and the registry's fixed conversions by `evidence.py`, coverage, and the evidence hash (C5).

## Rendering

- One visual claim per (category, phrase, hedge), in scene order: "The sampled east-sky frames show broken clouds in all 3 reviewed hour windows." `possible`, and `visible` at low confidence, render as "possibly show". Phrases come from fixed tables (sky cover, intensity, category).
- Absence only where it is provable: "No rain was visible in the sampled … frames in all 3 reviewed hour windows" when every reviewed window assessed that category as `not_observed` at medium or high confidence, no darkness or infrared limitation was reported, and the station did not measure rain (or lightning) for that category; a measured zero rain total only at full station coverage.
- Measured claims print station values only: temperature range, mean wind, strongest gust, rain total, lightning count when non-zero. Each says "over partial station coverage" when a cited metric's coverage is partial. A positive value never prints as zero ("less than 0.1 mm (less than 0.01 in)"), and one strike is singular.
- Counts read naturally ("the one reviewed hour window", "both reviewed hour windows", "1 of 3 reviewed hour windows"); a test runs the full enum cross product.
- Limitations are fixed sentences: unreviewed hours, no keogram, withheld station metrics, `not_assessable` categories per window count, lens and artifact categories, and the model's limitation codes.
- The summary is the claims joined; search window text is the same phrases per window. At most 16 claims and 8 limitations.
- `RENDER_REVISION` is hashed into every revision's prompt hash, so a template change is a new revision identity. A re-render after a measurement change keeps the model, prompt and schema hashes of the revision whose observations it reuses.
- When no hour passed visual review the text is station data only: the API serves `author: station` with the label "Station measurements only" and no model provenance, never the AI label.

## States

`complete` when every available window was reviewed; `degraded` (`visual_partial`, `visual_invalid` or `call_budget`) when some were not but claims exist; `failed` (`no_evidence`) when neither imagery nor measurements yield a claim. A measured-evidence change re-renders from the stored observations (refresh `measurements`) without calling the model.
