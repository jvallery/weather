# Third-party notices

Weather original code is MIT. Locked Python and JavaScript dependencies retain their own licenses,
authors, copyright notices, and license files in the distributed packages. The complete selected
package/version/license inventory, including build/check and optional platform dependencies, is
`supply-chain/licenses.json`. Metadata URLs identify the upstream package release.

The Python and Node runtime images include their upstream language and Debian notices.
The image SBOM is generated in fleet CI at build qualification (WX-01).

The fleet-only weather-ci image retains Playwright 1.64.1 and its Apache-2.0
notices in `/opt/weather-browser-tools/node_modules`, plus the downloaded
Chromium, Firefox and WebKit distributions and their notices in
`/opt/ms-playwright`. The exact JavaScript closure is locked separately in
`ci/browser/pnpm-lock.yaml`; those tools are not distributed in weather-web.

FFmpeg 9.0.2 with libx264 is GPL, not MIT. Its licenses are included under
`/opt/ffmpeg/share/licenses` in weather-app and the copied weather-ci toolchain;
see `docs/operations/ffmpeg.md` for the exact source
archive, checksum, configuration and corresponding-source distribution obligations.

The renderer (#15) uses NumPy (BSD-3-Clause, with bundled 0BSD/MIT/Zlib/CC0 parts) and Pillow
(MIT-CMU). Their binary wheels bundle native libraries (for NumPy, OpenBLAS; for Pillow,
libjpeg-turbo, libwebp, zlib and others), each with its license file inside the installed wheel.

Self-hosted IBM Plex Sans Variable and IBM Plex Mono fonts use the SIL Open Font License 1.1.
Their original notices are distributed at `/fonts/OFL-sans.txt` and `/fonts/OFL-mono.txt`;
exact approved font and notice hashes are recorded in `docs/research/font-provenance.json`.
Build/check dependencies with MPL-2.0 retain their notices; their source is not incorporated into
Weather's original source. The web build prunes development dependencies after the SSR build; production packages retain their
license files. The source inventory includes the complete build and development closure.
