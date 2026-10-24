#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
set -euo pipefail
[[ "${WEATHER_CI_IN_CLUSTER:-}" == 1 && "$(uname -s)" == Linux ]] || { echo 'Browser/media checks require approved in-cluster fleet CI' >&2; exit 2; }
cd "$(dirname "$0")/.."
# The shared Weather CI image bakes the exact locked browser closure and OS libraries.
# repo-validate runs as UID1001; it never installs system packages or elevates privileges.
pnpm --dir web exec node e2e/verify-browser-tools.mjs
if [[ "${1:-}" == --browsers-only ]]; then exit 0; fi
command -v ffmpeg >/dev/null || { echo 'The qualified Weather CI image lacks FFmpeg' >&2; exit 2; }
ffmpeg_version="$(ffmpeg -version)"
[[ "$ffmpeg_version" == "ffmpeg version 9.0.2"* ]] || { echo 'Use the qualified FFmpeg 9.0.2 tooling binary' >&2; exit 2; }
mkdir -p web/.test-media
# Synthetic green frames only; this is a browser transport fixture, never a camera/media qualification.
ffmpeg -hide_banner -loglevel error -f lavfi -i color=c=0x0e5a43:s=320x180:r=60 -t 60 -c:v libx264 -threads 1 -preset ultrafast -pix_fmt yuv420p -movflags +faststart -y web/.test-media/clip.mp4
ffmpeg -hide_banner -loglevel error -f lavfi -i color=c=0x0e5a43:s=320x180 -frames:v 1 -threads 1 -y web/.test-media/poster.jpg
