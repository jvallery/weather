# Contributing

Weather is in the planning phase. Start with [requirements](docs/requirements.md), [accepted product decisions](docs/decisions/2026-10-23-product-scope.md), the [planning audit](docs/research/adversarial-audit.md) and [issue #1](https://github.com/jvallery/weather/issues/1). Do not implement the application until the planning pass is complete.

Changes must identify the requirement, stable WX issue key, relevant decisions and observable acceptance. Preserve the fixed time, raw telemetry, privacy, durable publication and recovery contracts. Describe the smallest complete change and its validation; preserve unrelated work.

Use synthetic fixtures in public contributions. Do not publish camera credentials, station serials, precise coordinates, private network information, household imagery or raw operational receipts. Examples must use unmistakable placeholders. Report security issues through [SECURITY.md](SECURITY.md).

Original contributions use the [MIT license](LICENSE). Follow the [reuse policy](docs/decisions/license-and-reuse.md) for third-party source/assets. Record provenance and notices before importing.

The local CI/CD path is planned, not implemented. No workflow here currently qualifies application behavior. Once delivered, all gates must call the single documented check entrypoint; untrusted contributions use isolated synthetic validation without production access. Resource-intensive tests/builds run through the admitted cluster path. Documentation changes need diff/readback; meaningful data-integrity and security behavior needs focused regression evidence.
