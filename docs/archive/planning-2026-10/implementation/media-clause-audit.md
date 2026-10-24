# Rendering and GPU requirement audit

The [source-bound ledger](media-clause-audit.json) preserves every prose/table/fenced block of §§7.1–7.9 and 8.1–8.3. Acceptance below is grouped by concrete producer. This is design coverage, not executed media/runtime qualification.

Three distinct obligations must stay separate: required observed CPU fallback; required implementation/qualification of high interpolation; and optional GPU stage enablement. Publishing the observed fallback during an incident does not satisfy the unfinished high-interpolation gate. GPU physical enumeration does not prove sharing or scheduler access. The deferred composite is not an initial release prerequisite.

## §7.1 — WX-14

- Preserve full source-time intervals while distinguishing shutter support from acquisition coverage.
- 60 fps/synthetic blur cannot recover unobserved fast events; 180/360-degree shutter claims must not imply complete physical capture.

## §7.2 — WX-14

- Derive shutter edges from exact source center/step; default 180 degrees and compare 270/360 under commissioning.
- Linear-light float accumulation and actual overlap weights; no gamma-space intensity averaging.
- Bound observed temporal support across cadence gaps; track intended-versus-valid exposure weight and apply D10 0.90 cutoff without hiding sparse exposure.

## §7.3 — WX-14

- Always implement observed reference/CPU fallback and required high-interpolation qualification; 8 Hz remains optional.
- Reconstruct at absolute 4/8 samples per real second before exposure averaging, from nearby actual capture times; 2 Hz capture does not double the target grid.
- Preserve UTC-origin grid phase and jitter/missing slots through bounded chunks; reuse overlap, stream reconstruction and bound queues/RSS without full-day synthetic files or per-frame processes.
- Compare clouds/foliage/seams/precipitation/twilight/night; publish honest observed fallback on deadline/quality failure without declaring high-interpolation complete.

## §7.4 — WX-14, WX-16

- Pin verified current stable source and exact executable build; release filter availability must be probed rather than inferred from development docs.
- CPU minterpolate has software/pixel-format constraints; hwaccel/NVENC does not accelerate its flow. Qualify Vulkan features and platform limits separately for optional fruc_vulkan/frc_amf.
- Use explicit color/format transformations; tmix needs known spacing/window/phase; fps duplication is not motion inference.
- Enable deflicker/denoise/stabilization only with demonstrated benefit and full-field preservation; libplacebo mixing is not optical flow.
- NVENC/NVDEC and JPEG decode are distinct; external neural interpolation needs pinned backend/model/license and measured benefit.
- Record version/buildconf/filter/encoder/hardware listings and relevant help plus actual successful operation.

## §7.5 — WX-14

- Preserve complete discovered native geometry/SAR; no 16:9 or 3840 crop, unstitch or false-native upscale; new geometry means source epoch and explicit handling.
- Follow normalization/noise/reconstruction/linear integration/scale/overlay/encode order; preserve genuine lighting and OSD unless recorded mask policy.
- No interpolation over gaps, clock/profile/IR/PTZ/crop/scene changes; bracket bound and unavailable frame keep original global time map.
- Slot coverage and exposure coverage remain separate; explicit normal/partial/obvious-partial/unavailable defaults cannot be silently relaxed.
- Overlays use source-time historical weather/clock, not render-time current data; audio not required.

## §7.6 — WX-16, WX-19

- Mandatory native/web/mobile and genuine representative-frame posters; discover dimensions, even-size aspect-preserving scaling and no upscale.
- Qualify H.264 profile/level against actual macroblocks/rate/bitrate/references; smaller renditions preserve device compatibility.
- CPU libx264 preset/CRF/VBV/GOP commissioning distinct from NVENC quality settings; share normalized frames.
- Exactly N CFR frames, no audio, faststart, correct color transform/tags/SAR and both timescales 60000.
- Immutable same-origin MP4 HEAD/range/206/416/length/validators and native attachment bytes/filename; HTML download alone insufficient.
- HLS/DASH/new codecs remain optional later; no mandatory external player or streaming service.

## §7.7 — WX-16, WX-33

- Full-decode every rendition: count, 60/1 cadence, zero PTS, rational duration including terminal sample/movie, geometry/SAR/format/codec/profile/level/color and checksum.
- Inspect readable first/middle/last frames and identical source mapping across renditions.
- Synthetic fractional shutter/linear weighting/gap/boundary/chunk tests are distinct from real judder/flicker/banding/ghost/seam visual pilot.

## §7.8 — WX-36

- All three cameras/day-night produce six observed keograms with independently chosen versioned regions, especially front yard; retain orientation/masks/lineage.
- Fixed elapsed-UTC bins, bounded resolution and deterministic observed selection; visible missing bins, no stretch/AI/interpolation or adjacent-segment input.
- Versioned image/thumbnail/axis/gap assets and keyboard/click source-time seeks with precision/coverage.
- Compact extraction while inputs exist, durable pins and cleanup barrier; failed keogram remains explicit without withdrawing valid video.

## §7.9 — WX-45

- Follow-on only: one encoded east/west composite with distinct product/source relationships; preserve original products and cycle duration/partition.
- Research side-by-side/stacked layout, readable mobile/native geometry, synchronized source timing, unequal gaps, masks and compute/storage.
- No hidden missing view/time stretch; stitching requires calibrated overlap; initial linked charts need neither composite nor multiple native decoders.

## §8.1 — WX-15

- Inventory all nine physical devices, versions/APIs/owners/allocations/model parallelism/peak memory/SLOs privately, including protected camera device.
- Separate inventory, desired placement, scheduler admission, runtime visibility and utilization; no model/context/pairing displacement or camera borrowing.
- Investigate supported sharing and candidate envelope or evidence-backed CPU disposition; assigned/idle/shared labels are not admission and time slicing lacks memory/fault isolation.

## §8.2 — WX-14, WX-15

- Compare CPU, CPU+encode, optical-flow, JPEG-decode and optional neural stages independently with required performance/quality/resource measurements.
- Encoder features consume CUDA/memory; upload/reference/lookahead costs and resident demand remain in budget.
- Actual API/platform feature probes required; RTX2080 optical-flow hardware alone does not imply Vulkan support, nor JPEG input nvJPEG acceleration.
- Image surface arithmetic is a lower bound; add flow/intermediates/contexts/queue surfaces and measure peak, not assumed fixed budget.

## §8.3 — WX-15, WX-31, WX-33

- Compare resident alone/candidate alone/together at peak context, warmup/burst/steady/restart; collect latency/throughput/errors, host/pinned/GPU memory, engine/PCIe/thermal/Xid where available.
- Use actual resident SLO/abort conditions, start concurrency one, supported allocator/reservations/priority/monitoring; no fake capacity or hidden passthrough.
- CPU fallback must meet publication deadline with actual recipe; high-flow quality and optional GPU enablement are separate gates.
- Requalify after driver/build/model/mapping/resource changes; actual process termination and preserved resident service required on abort.

## Evidence and remaining decisions

[D10](../../../decisions/D10-media-recipe-and-qualification.md) defines the source-aware recipe, exposure threshold, bounded processing and observed keogram selection. [D09](../../../decisions/D09-gpu-and-cpu-admission.md) records the E04 all-nine-device census and supported-admission gaps. E06 establishes individual JPEG geometry only. None is a native-resolution render/quality/deadline or co-tenancy receipt.

Complete exact build/license/capability qualification, admitted CPU throughput, camera-specific privacy/keogram regions and scene comparisons. Optional GPU work additionally needs actual supported allocation and resident-service envelope. Preserve these as material planning gaps rather than relabeling an unqualified baseline as complete. No image build, heavy render, GPU job or device configuration was run by this audit.
