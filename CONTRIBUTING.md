# Contributing

Start with [AGENTS.md](AGENTS.md): how to build, test, contribute and release a change, and the hazards. Design context is in [docs/architecture.md](docs/architecture.md). Work items are the GitHub issues; an issue is ready when all of its native "blocked by" issues are closed.

Every change should name its issue number, preserve the fixed contracts (time model, raw telemetry, privacy and embargo, immutable publication, and recovery), and be the smallest complete change, validated with the cheapest reliable check.

Use synthetic fixtures. Never publish camera credentials, station serials, precise coordinates, private network information, household imagery or raw operational logs; examples use unmistakable placeholders. Report security issues through [SECURITY.md](SECURITY.md).

Pull requests from forks do not run on the self-hosted CI runners: a maintainer reviews the change and pushes it as a branch of this repository to run the checks.

Original contributions use the [MIT license](LICENSE). Third-party source and assets follow the [reuse policy](docs/decisions/license-and-reuse.md), with provenance and notices recorded before import.
