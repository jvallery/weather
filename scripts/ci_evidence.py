# SPDX-License-Identifier: MIT
"""Produce the three fixed, digest-bound files collected by fleet CI."""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

ROOT = Path(__file__).resolve().parent.parent
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_METADATA_BYTES = 1024 * 1024
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


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


def unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate evidence JSON key")
        result[key] = value
    return result


def checked_json(path: Path, maximum: int = MAX_JSON_BYTES) -> Any:
    if path.is_symlink() or not path.is_file():
        raise ValueError("Evidence must be a regular JSON file: " + path.name)
    size = path.stat().st_size
    if not 0 < size <= maximum:
        raise ValueError("Evidence JSON size is outside the limit: " + path.name)
    return json.loads(path.read_bytes(), object_pairs_hook=unique_keys)


def oci_inputs(base: Path, refs: dict[str, str], revision: str) -> dict[str, Path]:
    """Bind the platform's read-only, credential-free OCI inputs to this checkout."""
    if base.is_symlink() or not base.is_dir():
        raise ValueError("Expected the fixed OCI input directory")
    inputs = checked_json(base / "inputs.json", MAX_METADATA_BYTES)
    if (
        inputs.get("schema_version") != 1
        or inputs.get("repository") != "https://github.com/jvallery/weather.git"
        or inputs.get("revision") != revision
        or set(inputs.get("images", {})) != {"app", "web"}
    ):
        raise ValueError("OCI input identity differs from this checkout")
    paths = {}
    for role, reference in refs.items():
        entry = inputs["images"][role]
        digest = reference.split("@", 1)[1]
        if entry.get("reference") != reference or entry.get("root_manifest_digest") != digest:
            raise ValueError("OCI input differs from the commissioned image pin")
        path = base / role
        for directory in (path, path / "blobs", path / "blobs/sha256"):
            if directory.is_symlink() or not directory.is_dir():
                raise ValueError("OCI directories must not be links")
        if checked_json(path / "oci-layout", 1024) != {"imageLayoutVersion": "1.0.0"}:
            raise ValueError("Unsupported OCI layout")

        def metadata(identifier: str, directory: Path = path) -> Any:
            if not DIGEST.fullmatch(identifier):
                raise ValueError("Invalid OCI metadata digest")
            blob = directory / "blobs/sha256" / identifier.removeprefix("sha256:")
            value = checked_json(blob, MAX_METADATA_BYTES)
            if "sha256:" + hashlib.sha256(blob.read_bytes()).hexdigest() != identifier:
                raise ValueError("OCI metadata digest mismatch")
            return value

        selected = entry.get("manifest_digest", "")
        index = checked_json(path / "index.json", MAX_METADATA_BYTES)
        if (
            index.get("schemaVersion") != 2
            or len(index.get("manifests", [])) != 1
            or index["manifests"][0].get("digest") != selected
        ):
            raise ValueError("OCI index differs from the selected manifest")
        root = metadata(digest)
        if digest != selected:
            matches = [
                item
                for item in root.get("manifests", [])
                if item.get("platform", {}).get("os") == "linux"
                and item.get("platform", {}).get("architecture") == "amd64"
            ]
            if len(matches) != 1 or matches[0].get("digest") != selected:
                raise ValueError("OCI root does not bind one Linux amd64 manifest")
        manifest = metadata(selected)
        if manifest.get("config", {}).get("digest") != entry.get("config_digest"):
            raise ValueError("OCI configuration binding differs")
        config = metadata(entry["config_digest"])
        if config.get("os") != "linux" or config.get("architecture") != "amd64":
            raise ValueError("OCI configuration is not Linux amd64")
        layers = manifest.get("layers", [])
        descriptors = [
            {key: layer.get(key) for key in ("digest", "size", "mediaType")} for layer in layers
        ]
        if not layers or descriptors != entry.get("layers"):
            raise ValueError("OCI layer bindings differ")
        for layer in layers:
            identifier = layer.get("digest", "")
            if not DIGEST.fullmatch(identifier) or type(layer.get("size")) is not int:
                raise ValueError("Invalid OCI layer descriptor")
            blob = path / "blobs/sha256" / identifier.removeprefix("sha256:")
            if blob.is_symlink() or not blob.is_file() or blob.stat().st_size != layer["size"]:
                raise ValueError("OCI layer is missing, linked or truncated")
        paths[role] = path
    return paths


def main() -> None:
    if (
        sys.platform != "linux"
        or os.environ.get("WEATHER_CI_IN_CLUSTER") != "1"
        or not os.environ.get("KUBERNETES_SERVICE_HOST")
    ):
        raise SystemExit("Image evidence requires approved in-cluster CI")
    pins = yaml.safe_load((ROOT / "deploy/k8s/kustomization.yaml").read_text())
    refs = image_refs(pins)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    inputs = oci_inputs(Path("/ci-input"), refs, revision)
    output = ROOT / "artifacts/supply-chain"
    if output.parent.is_symlink():
        raise SystemExit("Evidence artifact directory must not be a symlink")
    if output.exists():
        raise SystemExit("Evidence output directory already exists; use a fresh CI checkout")
    # The platform archives this directory only for the dedicated fixed evidence check.
    # It adds the trusted Workflow/Pod UID and independent file digests after this command.
    output.mkdir(parents=True)
    subprocess.run(["bash", "scripts/ci.sh", "supply-chain"], cwd=ROOT, check=True, timeout=120)
    for role in refs:
        sbom = ROOT / f"artifacts/sbom-{role}.json"
        subprocess.run(
            ["syft", f"oci-dir:{inputs[role]}", "-o", f"cyclonedx-json={sbom}"],
            cwd=ROOT,
            check=True,
            timeout=600,
        )
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
