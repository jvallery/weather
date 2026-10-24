# D09 — CPU baseline and bounded GPU qualification

> **Amended 2026-10-24** by the [2026-10-24 simplification record](2026-10-24-simplification.md): O2/P18: the census stands; GPU use is deferred. WX-15 is now the interpolation A/B pilot and cortex-ai-compute#403 is closed. Where this ADR conflicts with that record or [architecture.md](../architecture.md), they win.

Status: **all-nine-device census verified; no Weather GPU or CPU workload admitted**. Requirements R09/R10/R28 and §8; consumers WX-14/15/31/33. Compute owner: [OWN-GPU](https://github.com/jvallery/cortex-ai-compute/issues/403). CPU performance qualification remains D10 with OWN-CI/host capacity admission. No resource, model or deployment changed during this investigation.

## Decision

Implement and qualify the mandatory CPU render path. Keep GPU acceleration conditional, with CPU temporal work plus bounded encode-only NVENC as the first proposed GPU stage. Do not default to optical-flow/neural work on resident inference devices. This is a candidate ordering, not an admitted execution slot or a reason to omit high-interpolation implementation.

Request an owner-supported exact-device allocation mechanism and resident-service operating envelope before any GPU experiment. Do not patch node capacity, inject device paths, enable global time slicing or evict resident models to make the experiment fit. The protected camera device is excluded entirely. If the sharing mechanism cannot be admitted, retain CPU operation and the explicit future qualification issue; CPU deadline and high-interpolation quality still need actual evidence.

## E04 live census

At 2026-10-23T20:41:22Z, bounded read-only `nvidia-smi` queries on all five GPU workers returned nine devices. All nine UUIDs matched the pinned compute inventory, including the separately protected camera device. Every device reported driver 580.178.04. Exact UUIDs, nodes, pod/resource identities and raw receipts remain in the private compute-owner issue.

| Device group | Count | Reported total MiB per device | Used MiB per device at snapshot | Declared resident role | Weather disposition |
|---|---:|---|---|---|---|
| RTX 3060 | 2 | 12,288 | 9,555 / 9,555 | OCR pool | Encode-only candidate only after owner qualification; allocated model memory is not spare capacity. |
| RTX 4070 Ti SUPER | 2 | 16,376 | 14,998 / 14,998 | Paired text inference | Preserve pair and resident context; no initial experiment placement proposed. |
| RTX 2080 | 1 | 8,192 | 5,168 | Reranking | Possible encode-only candidate; do not infer Vulkan optical-flow support or admission from free memory. |
| RTX 5080 | 2 | 16,303 | 15,830 / 15,830 | Paired multimodal inference | Only 473 MiB observed free on each; no initial experiment placement proposed. |
| RTX 4060 Ti | 1 | 16,380 | 13,339 | Embedding service | Preserve dedicated embedding allocation; no initial experiment placement proposed. |
| RTX 5070 | 1 | 12,227 | 7,912 | Protected camera processing | Excluded; no sharing, eviction or qualification workload. |

Declared roles come from source placement; memory/utilization come from the live query. Zero sampled utilization on the eight AI devices does not establish idle service, peak KV-cache demand, encoder availability, memory headroom or an available scheduling window. The camera device reported 73% utilization. This is one observation, not a load study or performance baseline.

Kubernetes reports generic GPU capacities totaling seven on the first four workers, plus two separate exact-device resources on the protected worker. No node advertises `nvidia.com/gpu.shared`. Existing running pods still declare that resource, so their continued operation cannot qualify new-consumer scheduling. Live stock device-plugin readiness covers four workers; the dedicated UUID allocator is Ready on the protected worker. The stock plugin has no mounted ConfigMap in this readback.

The pinned source overlay describes generic 1:1 allocation and historical manually patched shared capacity. Its old issue reference is closed/superseded. Treat that as historical explanation, not a supported current sharing grant. OWN-GPU must reconcile desired placement, current plugin inventory and existing pods before selecting an experiment mechanism. This audit does not repair shared infrastructure incidentally.

All eight nodes were Ready with no MemoryPressure, DiskPressure or PIDPressure at observation; control-plane nodes remain cordoned. Those facts do not establish CPU admission. The private snapshot includes current scheduled workload resource declarations, but a correct capacity decision also requires effective init/overhead requests, quotas/reservations, working sets, N-1 policy and owner confirmation. No free-CPU claim is made here.

Source revisions: compute `ccc845a75551b0de97ee3a91613fd3b553704a05`; host inventory `5f0d438dc7f10b0aad88d6375a3e68644e65c234`. E04's private census receipt is retained with OWN-GPU; SHA-256 `07089368f2d590635377312b71e2262c6d95c2d63d87e2e83819c35e41317919`. Snapshot hardware enumeration is complete; operating-envelope and admission research remain incomplete.

## Qualification order and stop conditions

1. Establish an admitted isolated CPU job path and source-image fixture custody. Measure native-resolution six-output throughput, peak memory and I/O at the selected recipe; preserve existing services and healthy/recovery capacity. A local Mac render is not the fallback qualification path.
2. Compute owner selects one device/placement, supported allocation, bounded surfaces and concurrency one. The two candidate groups above are a research shortlist only. Measure resident service alone, encode alone, then combined under realistic peak workload, warm-up and restart.
3. Bind service-specific p95/p99/throughput/error, peak VRAM/host/pinned-memory and thermal/Xid limits to the resident owner's accepted SLOs. Abort on their defined violation and prove the Weather process/device allocation actually terminates. Do not invent a universal acceptable percentage of degradation.
4. Compare encode-only with CPU end-to-end cost/quality, including upload/surface/reference memory and bandwidth. Dedicated encoder engines do not imply isolation from inference. Only then consider separately admitted optical-flow or decode acceleration; optional neural interpolation needs additional measured benefit and model/license custody.
5. Record driver/plugin/allocator/FFmpeg/model/image identities, exact allocation and source fixture, throughput, quality, peak resources, resident impacts and rollback. Requalify after material version, model or placement changes. Keep high-flow and GPU enablement gates distinct from the ability to publish an honest CPU observed-exposure fallback.

No candidate is admitted by a healthy pod or this census. Rollback of a future trial stops only its exact job and returns its allocation, then checks resident behavior; it never restarts or relocates resident services as a hidden cleanup step.
