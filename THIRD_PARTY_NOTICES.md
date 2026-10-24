# Third-party notices

Weather original code is MIT. Locked Python and JavaScript dependencies retain their own licenses,
authors, copyright notices, and license files in the distributed packages. The complete selected
package/version/license inventory, including build/check and optional platform dependencies, is
`supply-chain/licenses.json`. Metadata URLs identify the upstream package release.

The Python and Node runtime images include their upstream language and Debian notices.
The image SBOM is generated in fleet CI at build qualification (WX-01).

Capture uses simplejpeg 1.9.0 and NumPy 2.5.4. This software is based in part on
the work of the Independent JPEG Group. Their full wheel notices, including
libjpeg-turbo, OpenBLAS/LAPACK, GCC runtime exception and libquadmath terms, are
retained in `supply-chain/capture-decoder` and the installed package license
directories. See `docs/decisions/capture-decoder-reuse.md` for the exact artifacts
and binary distribution obligations.

FFmpeg 9.0.2 with libx264 is GPL, not MIT. Its licenses are included under
`/opt/ffmpeg/share/licenses` in weather-app; see `docs/operations/ffmpeg.md` for the exact source
archive, checksum, configuration and corresponding-source distribution obligations.

Self-hosted IBM Plex Sans Variable and IBM Plex Mono fonts use the SIL Open Font License 1.1.
Their original notices are distributed at `/fonts/OFL-sans.txt` and `/fonts/OFL-mono.txt`;
exact approved font and notice hashes are recorded in `docs/research/font-provenance.json`.
Build/check dependencies with MPL-2.0 retain their notices; their source is not incorporated into
Weather's original source. The web build prunes development dependencies after the SSR build; production packages retain their
license files. The source inventory includes the complete build and development closure.
