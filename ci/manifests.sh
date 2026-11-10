#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Render deploy/k8s with a pinned kustomize (same check as the native "manifests" step).
set -euo pipefail
cd "$(dirname "$0")/.."
version=v5.7.1
sha=ea375e7372f9aa029129d4b2d16c66b7750b7f1213c4f66f910d981c895818d8
tools="${RUNNER_TEMP:-${TMPDIR:-/tmp}}/weather-kustomize-${version}"
if [[ ! -x "$tools/kustomize" ]]; then
  mkdir -p "$tools"
  curl -fsSL --retry 3 -o "$tools/k.tgz" \
    "https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize%2F${version}/kustomize_${version}_linux_amd64.tar.gz"
  echo "${sha}  $tools/k.tgz" | sha256sum -c - >/dev/null
  tar -xzf "$tools/k.tgz" -C "$tools" kustomize
fi
rendered="$("$tools/kustomize" build deploy/k8s)"
echo "deploy/k8s renders"
# weather#223: every pipeline WEATHER_IMAGE_DIGEST equals the weather-app digest pin.
pin="$(awk '/^- name: weather-app$/{f=1;next} f&&/digest:/{print $2;exit}' deploy/k8s/kustomization.yaml)"
[[ "$pin" =~ ^sha256:[0-9a-f]{64}$ ]] || { echo "weather-app digest pin not found" >&2; exit 1; }
values="$(grep -A1 -- '- name: WEATHER_IMAGE_DIGEST' <<<"$rendered" | awk '/value:/{print $2}')"
[[ "$(wc -l <<<"$values" | tr -d ' ')" == 3 && "$(sort -u <<<"$values")" == "$pin" ]] \
  || { echo "WEATHER_IMAGE_DIGEST differs from the weather-app pin ${pin}: ${values//$'\n'/ }" >&2; exit 1; }
echo "pipeline WEATHER_IMAGE_DIGEST x3 == weather-app pin ${pin:0:19}"
