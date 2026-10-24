# Contributing

Agents and maintainers start with [AGENTS.md](AGENTS.md). It covers the definition of done, how to pick ready work and the hazards. Design context is in [docs/architecture.md](docs/architecture.md) and the [simplification record](docs/decisions/2026-10-24-simplification.md). Work items are the GitHub issues; an issue is ready when all of its native "blocked by" issues are closed.

Every change should:

- name its WX issue key and requirement IDs;
- preserve the fixed contracts: time model, raw telemetry, privacy and embargo, immutable publication, and recovery;
- be the smallest complete change, validated with the cheapest reliable check.

Use synthetic fixtures in public contributions. Never publish camera credentials, station serials, precise coordinates, private network information, household imagery or raw operational receipts. Examples must use unmistakable placeholders. Report security issues through [SECURITY.md](SECURITY.md).

Original contributions use the [MIT license](LICENSE). Third-party source and assets follow the [reuse policy](docs/decisions/license-and-reuse.md), with provenance and notices recorded before import.

Checks run through `scripts/ci.sh` in fleet CI, inside the cluster. Builds, integration suites and media pilots never run on a workstation. **Pull requests from forks are not executed by fleet CI.** A maintainer reviews the change and re-pushes it as a same-repository branch to run checks.
