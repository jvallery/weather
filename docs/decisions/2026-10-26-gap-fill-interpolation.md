# Gap fill: keep the linear cross-fade (#16) — 2026-10-26

**Status:** accepted under Jason's rule of 2026-10-26: adopt motion-compensated interpolation
only if it is visibly better on clouds and costs at most 2× the CPU of `observed-exposure/v2`'s
fill; otherwise keep the cross-fade. It amends [D10](D10-media-recipe-and-qualification.md):
observed exposure with `gap-fill/v1` (#144) is the production profile, and no high-interpolation
profile is built.

## Question

#144 replaced black slates with partial-coverage frames, a linear cross-fade across short gaps
(≤12 frames) and a labelled held frame across long ones. #16 asked whether FFmpeg
motion-compensated interpolation fills the short gaps visibly better on clouds, and at what
CPU cost.

## Method

A disposable in-cluster Job (`weather` namespace, non-root, no credentials, corpus mounted
read-only) took two preserved skycam_west hours from staging `corpus/16/`: 2026-10-25T18Z
(midday cumulus) and 2026-10-26T00Z (dusk). For each, the first 900 captured frames (30 minutes
at 0.5 Hz, scaled to 960×540) were the ground truth. It held out 133 real frames: single misses
at the 8.5% rate seen on 2026-10-25, plus 30 runs of 2–5 frames. It refilled them two ways and
scored each refill against the real frame it replaced:

- **A, cross-fade:** `gap-fill/v1`'s integer linear blend between the nearest present
  neighbours.
- **B, motion-compensated:** per held-out run, `minterpolate=fps=L+1:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1`
  across the two present neighbours.

CPU is process-plus-children user and system time (`getrusage`) for the fill step only, on
identical decoded input. Holding out real frames scores both methods against what the camera
actually saw, which a side-by-side of two synthetic outputs cannot do.

## Results

| Hour | Method | Fill CPU-s | Single frames PSNR / SSIM | Runs of 2–5 PSNR / SSIM |
|---|---|---|---|---|
| 10-25 18Z clouds | A cross-fade | 1.46 | 39.96 dB / 0.9985 | 36.16 dB / 0.9931 |
| | B minterpolate | 111.16 | 40.32 dB / 0.9988 | 37.04 dB / 0.9934 |
| 10-26 00Z dusk | A cross-fade | 1.87 | 40.35 dB / 0.9979 | 38.72 dB / 0.9966 |
| | B minterpolate | 111.75 | 40.03 dB / 0.9979 | 38.75 dB / 0.9969 |

B costs 60–76× A's CPU. Its best gain is 0.9 dB on multi-frame runs in the cloud hour; at dusk
the two methods match. Differences under about 1 dB at SSIM above 0.99 are below what playback
at timelapse speed shows; the stacked truth|A|B clip per hour is kept for review. Extrapolated to
the 2026-10-25 miss rate, B would add about 50 CPU-minutes per camera-day (about 2.6 CPU-hours
across three cameras); #145 lowers that miss rate, which shrinks both costs.

## Decision

Keep `gap-fill/v1`'s cross-fade. B fails the CPU bound by a factor of about 30 and is not
visibly better. No follow-up implementation issue is filed. RIFE, GPU and 4×/8× reconstruction
profiles stay out of scope (O2).

The pilot outputs (`result.json` and the clip per hour) and the corpus stay in private staging
custody under `corpus/16/`. They are deleted with #4's corpus after #49 closes.
