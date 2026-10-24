# Development and checks

Toolchains: CPython 3.14.9, uv 0.13.5, Node 24.22.1, pnpm 12.15.0, gitleaks 8.30.1, Syft 1.55.0 (image evidence only).
Install these through the existing operator/toolbox paths. Run `uv sync --frozen`, then
`cd web && pnpm install --frozen-lockfile --ignore-scripts`.

On the Mac, only `scripts/ci.sh lint typecheck unit` and the image-free supply-chain scan are allowed.
`integration`, `all`, image builds, SBOM generation and browser suites run in fleet CI only;
set `WEATHER_CI_IN_CLUSTER=1` in the approved submission profile. No integration cases exist yet.
The approved runner entrypoint is `WEATHER_CI_IN_CLUSTER=1 bash scripts/bootstrap-ci.sh bash scripts/ci.sh all`;
it installs checksum-verified tools and the uv-managed pinned interpreter into disposable `/tmp` storage.

The Python production requirements are generated from the lock with
`uv export --frozen --no-dev --no-emit-project --no-header --no-annotate --quiet > requirements.lock`.
After changing locks, review upstream metadata and run
`uv run python scripts/license_inventory.py --update`. The inventory includes every package version
in the uv and pnpm locks, including optional platform packages and package-manager dependencies.
`--manifest path.json` checks a synthetic license fixture without changing the inventory.
MPL-2.0 is allowed for build/check dependencies (pathspec and tooling); no MPL source is imported into
Weather. CC-BY-4.0 metadata remains attributed in the inventory. Binary OS/media licenses are covered
by image SBOMs and the FFmpeg distribution notes.

The API runs with `python -m weather.cli api`, HTTP on 8000 and metrics on 9000.
`PORT` and `METRICS_PORT` override these. `/healthz`, `/api/healthz` and `/readyz` are scaffold health
checks. Metrics are isolated on their own listener and are never routed publicly.
The web image starts `node server.mjs`, HTTP on 3000 and metrics on 9000.
Both images run UID/GID 10001, need no service-account token, and support a read-only root filesystem.
Runtime assets and React scripts are served locally; no CDN or remote font is configured.

Import the Dockerfile's exact upstream Python/Node digests through the approved `weather-python-base`
and `weather-node-base` registry profiles before WX-01 builds, using `images/upstream-imports/`.
Final app/web bases use the resulting verified `registry.vallery.net/jvallery/weather-*-base` digests.

TypeScript 7.0.2 is installed as `@typescript/native` and owns the `tsc` executable; `tsc --noEmit --singleThreaded`
checks the app. ESLint uses the JavaScript API from `@typescript/typescript6` 6.0.2 via the `typescript` alias,
following [Microsoft's side-by-side recipe](https://devblogs.microsoft.com/typescript/announcing-typescript-7-0/#running-side-by-side-with-typescript-6.0).
This preserves the selected native compiler while supporting API-based tooling.

`mypy-extensions` 1.1.0 omits license metadata. Its distributed wheel LICENSE was reviewed as MIT;
SHA-256 of that LICENSE: `a50450da1d53cd777b80ced77c58ff96abe0ccd879706bd142c3ec20e245f0b4`.
The inventory generator has an exact name/version exception for this reviewed release.

The isolated Hatchling build backend and its complete dependency closure are pinned with uv build constraints
and included in the development lock and license inventory. Production requirements exclude build-only tools.

`hatchling` 1.29.0 also omits upstream metadata. Its wheel LICENSE.txt was reviewed as MIT;
SHA-256: `7f143a8127ad4873862d70854b5bd2abd0085aa73e64fd2b08704a3b9f5c07fc`.
The inventory generator restricts this exception to that exact release.

The image scan writes `artifacts/sbom-app.json` or `artifacts/sbom-web.json` and its SHA-256 sidecar.
WX-01 owns artifact custody and the evidence producer; the scaffold does not submit a separate scan workflow.
