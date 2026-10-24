# CI supply-chain evidence

The agents-owned registry defines `application`, `manifests`, and
`supply-chain-evidence`. The generated `.agent-fleet/ci.yaml` mirrors that
contract; update the registry and distribute its generated export.

The evidence check scans the exact app/web deployment image pins. Trusted init
verifies their OCI bytes and source revision, clears Git credential transport
before registry access, and hands the checkout to UID 1001 after verification.
The candidate receives read-only OCI directories and no registry or archive
Secret mount. It produces `sbom-app.json`, `sbom-web.json`, and `licenses.json`.
The fixed platform wrapper adds `manifest.json`; Argo uploads four required raw
JSON objects. Ordinary checks declare no artifacts and logs remain unarchived.

For verification:

1. Capture the normal CI Workflow UID, exact source SHA, and evidence Pod
   name/UID before Pod garbage collection. Require the terminal Workflow and
   all three real checks to succeed. Preserve failed attempts and their IDs.
2. Read the producer node's `evidence-manifest-sha256` output and four artifact
   pointers. Their fixed key prefix is
   `native-ci/v1/weather/supply-chain/<workflow-uid>/<pod-name>/`.
3. Use the existing independent read-only storage identity to GET those exact
   four keys. Inside the consuming process, compare the manifest SHA with the
   node output, its source/Workflow/Pod identities with captured Kubernetes
   state, and the exact file allowlist, bounds, every byte size, and SHA-256.
   Confirm nonempty CycloneDX components. Record metadata only; never print
   credentials, Secret data, authorization headers, or artifact bodies.
4. Keep the bounded receipt in Git. The
   [2026-10-24 qualification](evidence/ci-supply-chain-2026-10-24.json) records
   actual main and PR runs, independent checksums, and the earlier failures.

The platform owner uses Argo semantic lint against new immutable references
before updating stable aliases, and waits for zero active stable consumers.
Kubernetes CRD dry-run alone does not perform that semantic validation. See the
[platform runbook](https://github.com/jvallery/agents/blob/main/docs/fleet-ci-weather-evidence.md)
for source-owned templates and operator access.

The existing storage contract provides a seven-day hot window and owner-managed
backup. These receipts prove upload and independent readback at the recorded
times. They do not prove a completed backup/restore, indefinite retention, or
DR. The recorded runs used the generic CI tools image; PostgreSQL/browser
runtime qualification of the separate shared Weather CI image is separate.
