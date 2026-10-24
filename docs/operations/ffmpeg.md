# FFmpeg distribution and build recipe

WX-02 pins FFmpeg **9.0.2** from <https://ffmpeg.org/releases/ffmpeg-9.0.2.tar.xz>.
SHA-256: `8c3850283eb25fa026482078a04051e0be17347b09ef81a0849bec15a96e002e`.
The release archive was downloaded and hashed before this recipe was committed.
`Dockerfile.app` verifies this hash before extraction. It never compiles on the Mac.

Configure flags: `--prefix=/opt/ffmpeg --enable-gpl --enable-libx264 --disable-debug --disable-doc --disable-ffplay`.
No nonfree components are enabled. The compiler and x264 dependency closure comes from the fixed
Debian bookworm snapshot `20261023T000000Z`; its package versions and the configure output must be
retained in the in-cluster build log. The amd64 runtime copies libx264 from that same build stage.

Enabling libx264 makes the resulting FFmpeg distribution GPL, separate from Weather's MIT source.
Every distributed image includes the FFmpeg license files, Weather notices, this recipe, and the
upstream source URL/checksum. When distributing the binary, provide the exact corresponding FFmpeg
source archive and the Debian libx264 source package from the pinned snapshot, including any patches,
configuration and build instructions; preserve GPL notices. Do not represent the image as MIT-only.
The source is unmodified. No application source is copied from FFmpeg or x264.

WX-01 (#2) owns actual in-cluster compilation, executable/capability probes, SBOM generation,
archive custody and egress-denied startup verification. A committed Dockerfile proves none of those.

The compiler stage uses main, bookworm-updates and bookworm-security from the same fixed
Debian snapshot date. All three suites are needed because the digest-pinned Python base
already includes security updates; main alone can require an older exact-version dependency
(for example perl versus perl-base), causing apt to fail before compilation.
