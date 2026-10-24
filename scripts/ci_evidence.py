# SPDX-License-Identifier: MIT
"""Produce the three fixed, digest-bound files collected by fleet CI."""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
MAX_JSON_BYTES = 16 * 1024 * 1024


def image_refs(document: dict[str, Any]) -> dict[str, str]:
    """Accept only the two commissioned Weather runtime image pins."""
    refs: dict[str, str] = {}
    for role in ("app", "web"):
        alias = f"weather-{role}"
        entries = [item for item in document.get("images", []) if item.get("name") == alias]
        if len(entries) != 1:
            raise ValueError(f"Expected exactly one commissioned {alias} pin")
        entry = entries[0]
        registry_name = f"registry.vallery.net/jvallery/{alias}"
        digest = entry.get("digest", "")
        if (
            entry.get("newName") != registry_name
            or entry.get("newTag")
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
        ):
            raise ValueError(f"Invalid digest-only {alias} pin")
        refs[role] = f"{registry_name}@{digest}"
    return refs


def checked_json(path: Path) -> Any:
    if path.is_symlink() or not path.is_file():
        raise ValueError("Evidence must be a regular JSON file: " + path.name)
    size = path.stat().st_size
    if not 0 < size <= MAX_JSON_BYTES:
        raise ValueError("Evidence JSON size is outside the limit: " + path.name)
    return json.loads(path.read_bytes())


def main() -> None:
    if (
        sys.platform != "linux"
        or os.environ.get("WEATHER_CI_IN_CLUSTER") != "1"
        or not os.environ.get("KUBERNETES_SERVICE_HOST")
    ):
        raise SystemExit("Image evidence requires approved in-cluster CI")
    pins = yaml.safe_load((ROOT / "deploy/k8s/kustomization.yaml").read_text())
    refs = image_refs(pins)
    output = ROOT / "artifacts/supply-chain"
    if output.parent.is_symlink():
        raise SystemExit("Evidence artifact directory must not be a symlink")
    if output.exists():
        raise SystemExit("Evidence output directory already exists; use a fresh CI checkout")
    # The platform archives this directory only for the dedicated fixed evidence check.
    # It adds the trusted Workflow/Pod UID and independent file digests after this command.
    output.mkdir(parents=True)
    for role, image in refs.items():
        subprocess.run(
            ["bash", "scripts/ci.sh", "supply-chain", "--image", image],
            cwd=ROOT,
            check=True,
            timeout=600,
        )
        sbom = ROOT / f"artifacts/sbom-{role}.json"
        data = checked_json(sbom)
        if not isinstance(data, dict) or data.get("bomFormat") != "CycloneDX":
            raise ValueError("Expected a CycloneDX JSON SBOM")
        if not data.get("components"):
            raise ValueError("Image SBOM contains no package components")
        shutil.copyfile(sbom, output / sbom.name)
    licenses = ROOT / "supply-chain/licenses.json"
    checked_json(licenses)
    shutil.copyfile(licenses, output / "licenses.json")
    print(json.dumps({"image_refs": refs, "files": sorted(p.name for p in output.iterdir())}))


if __name__ == "__main__":
    main()
