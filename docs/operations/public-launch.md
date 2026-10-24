# Public launch verification

Run `python scripts/verify_public_launch.py --output /path/to/receipt.json` for the bounded,
read-only header/private-route smoke against the public Tunnel. It checks Today, history, a real
404, and closed editor/operator/metrics paths. It uses an explicit verifier User-Agent, inspects
at most 2 MiB per response, and writes only status, byte counts, header presence and fixed failure
labels. It never writes response bodies, matched private text or credentials into its receipt.

Exit 1 means an acceptance condition failed; it does not weaken the check for an undeployed route.
The initial skeleton is expected to fail for missing history and CSP until WX-20 is delivered.
Fix the responsible implementation and rerun against the intended deployed revision. Privacy
pattern inspection complements the typed public API allowlist and cannot prove arbitrary prose
contains no private information.

This smoke does not establish fresh Tempest data, accessibility, image/revision equality,
24-hour probe continuity or recovery. WX-47 also requires the real in-cluster browser/axe checks,
three current-source-age checks a minute apart, retained Prometheus series, the CNPG/PITR receipt,
and authoritative Argo/pod/source-pin readback. Keep those receipts separate.
