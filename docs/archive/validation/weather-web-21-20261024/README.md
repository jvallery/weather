# Weather #21 validation archive

Immutable copies of the 27 successful fixture-browser screenshots (7,114,905 bytes),
the original capture manifest/final report and the credential-free operator readback helper.
`SHA256SUMS.json` records every byte count and SHA-256. Images are safe synthetic fixtures;
there are no private camera originals, exact coordinates, addresses or station serials.
All 27 were visually reviewed. This archive uses a named non-main branch and no PR.

The successful fixture qualification was `weather-web-browser-20261024-06`, source
`8c2ccdfff5394e3c5cd381d10e9d49f6c2f2b1a8`: 96 passed across Chromium/WebKit/Firefox.
Fixture capture READY is not production old-tab READY. No production rolling proof or
web-image promotion completed. Source #60 merged at `e3d4151…`; draft promotion PR72
at `ff74fb1…` remains green and unmerged. The actual web digest at shutdown is `0761f4eb…`;
the built candidate is `7ff8819a…`.

**Work is paused by Jason for shutdown. Do not execute the helper, submit jobs, merge or
promote until Jason explicitly resumes.** The helper contains no credentials or private
identifiers; it consumes explicit operation/source/digest/UID arguments and existing ROOT
Kubernetes access. It is syntax-checked source, not an executed production-rollout result.
Read its fail-closed checks and `--help` before using it after resumption.

Resolve the real edge blocker, network-infra#799, first. The initial RUM create returned409
with provider state unchanged; its supported Weather-only Configuration Rule replacement
is source WIP, not delivered. Then refresh actual source, digests, capacity and ownership;
use a NEW stable held-tab operation, verify READY, coordinate protected PR72 promotion,
and require exact Argo/both-running-replica/origin-asset/public-browser proof before closure.

Full shutdown and restart record:
https://github.com/jvallery/weather/issues/21#issuecomment-6386755637
https://github.com/jvallery/weather/pull/72#issuecomment-6386755881
Owner handoff:
https://github.com/jvallery/network-infra/issues/799#issuecomment-6386725579
