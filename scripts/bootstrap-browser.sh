#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
set -euo pipefail
[[ "${WEATHER_CI_IN_CLUSTER:-}" == 1 && "$(uname -s)" == Linux ]] || { echo 'Browser/media checks require approved in-cluster fleet CI' >&2; exit 2; }
cd "$(dirname "$0")/.."
pnpm --dir web exec playwright install --with-deps chromium webkit firefox
if [[ "${1:-}" == --browsers-only ]]; then exit 0; fi
if ! command -v ffmpeg >/dev/null; then
  command -v apt-get >/dev/null || { echo 'The CI image lacks the supported fixture encoder installation path' >&2; exit 2; }
  apt-get update
  apt-get install -y --no-install-recommends ffmpeg
fi
mkdir -p web/.test-media
# Synthetic green frames only; this is a browser transport fixture, never a camera/media qualification.
ffmpeg -hide_banner -loglevel error -f lavfi -i color=c=0x0e5a43:s=320x180:r=60 -t 60 -c:v libx264 -threads 1 -preset ultrafast -pix_fmt yuv420p -movflags +faststart -y web/.test-media/clip.mp4
ffmpeg -hide_banner -loglevel error -f lavfi -i color=c=0x0e5a43:s=320x180 -frames:v 1 -threads 1 -y web/.test-media/poster.jpg
