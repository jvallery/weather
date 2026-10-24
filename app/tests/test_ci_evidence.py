# SPDX-License-Identifier: MIT
import hashlib
import json
import runpy
import sys
from pathlib import Path

import pytest

HELPERS = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts/ci_evidence.py"))


def pins() -> dict[str, list[dict[str, str]]]:
    return {
        "images": [
            {
                "name": f"weather-{role}",
                "newName": f"registry.vallery.net/jvallery/weather-{role}",
                "digest": "sha256:" + "1" * 64,
            }
            for role in ("app", "web")
        ]
    }


def test_evidence_cannot_scan_another_repository_or_a_mutable_tag() -> None:
    for replacement in (
        {"newName": "registry.vallery.net/jvallery/another-app"},
        {"newTag": "latest"},
        {"digest": "not-a-digest"},
    ):
        document = pins()
        document["images"][0].update(replacement)
        with pytest.raises(ValueError, match="digest-only"):
            HELPERS["image_refs"](document)


def test_duplicate_runtime_pin_is_rejected_before_scanning() -> None:
    document = pins()
    document["images"].append(document["images"][0].copy())
    with pytest.raises(ValueError, match="exactly one"):
        HELPERS["image_refs"](document)


def test_evidence_input_cannot_be_a_symlink_or_oversized_file(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    source.write_text('{"bomFormat":"CycloneDX"}')
    link = tmp_path / "evidence.json"
    link.symlink_to(source)
    with pytest.raises(ValueError, match="regular JSON"):
        HELPERS["checked_json"](link)
    with source.open("wb") as handle:
        handle.truncate(HELPERS["MAX_JSON_BYTES"] + 1)
    with pytest.raises(ValueError, match="size"):
        HELPERS["checked_json"](source)


def fixture_inputs(base: Path, *, multiarch: bool = False) -> tuple[dict[str, str], str]:
    revision = "2" * 40
    images = {}
    refs = {}
    for role in ("app", "web"):
        directory = base / role
        blobs = directory / "blobs/sha256"
        blobs.mkdir(parents=True)

        def blob(value: bytes, directory: Path = blobs) -> str:
            digest = "sha256:" + hashlib.sha256(value).hexdigest()
            (directory / digest.removeprefix("sha256:")).write_bytes(value)
            return digest

        config = blob(b'{"os":"linux","architecture":"amd64"}')
        layer_bytes = b"bounded fixture bytes, never extracted"
        layers = [{"digest": blob(layer_bytes), "size": len(layer_bytes), "mediaType": "layer"}]
        manifest = blob(
            json.dumps(
                {"schemaVersion": 2, "config": {"digest": config}, "layers": layers}
            ).encode()
        )
        selected = {"digest": manifest, "platform": {"os": "linux", "architecture": "amd64"}}
        index = {"schemaVersion": 2, "manifests": [selected]}
        root = blob(json.dumps(index).encode()) if multiarch else manifest
        refs[role] = f"registry.vallery.net/jvallery/weather-{role}@{root}"
        images[role] = {
            "reference": refs[role],
            "root_manifest_digest": root,
            "manifest_digest": manifest,
            "config_digest": config,
            "layers": layers,
        }
        (directory / "index.json").write_text(json.dumps(index))
        (directory / "oci-layout").write_text('{"imageLayoutVersion":"1.0.0"}')
    (base / "inputs.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "repository": "https://github.com/jvallery/weather.git",
                "revision": revision,
                "images": images,
            }
        )
    )
    return refs, revision


@pytest.mark.parametrize("multiarch", [False, True])
def test_fixed_oci_inputs_bind_checkout_pin_and_selected_manifest(
    tmp_path: Path, multiarch: bool
) -> None:
    refs, revision = fixture_inputs(tmp_path, multiarch=multiarch)
    assert HELPERS["oci_inputs"](tmp_path, refs, revision) == {
        role: tmp_path / role for role in ("app", "web")
    }


@pytest.mark.parametrize("mismatch", ["revision", "repository", "pin", "roles"])
def test_foreign_or_stale_oci_inputs_are_rejected(tmp_path: Path, mismatch: str) -> None:
    refs, revision = fixture_inputs(tmp_path)
    path = tmp_path / "inputs.json"
    data = json.loads(path.read_bytes())
    if mismatch in {"revision", "repository"}:
        data[mismatch] = "different"
    elif mismatch == "pin":
        data["images"]["app"]["reference"] = refs["web"]
    else:
        data["images"]["unexpected"] = data["images"]["web"]
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="identity|image pin"):
        HELPERS["oci_inputs"](tmp_path, refs, revision)


@pytest.mark.parametrize("mutation", ["metadata", "index", "layer_link", "layer_size"])
def test_oci_chain_mutations_fail_before_scanning(tmp_path: Path, mutation: str) -> None:
    refs, revision = fixture_inputs(tmp_path)
    inputs = json.loads((tmp_path / "inputs.json").read_bytes())
    entry = inputs["images"]["app"]
    if mutation == "metadata":
        path = tmp_path / "app/blobs/sha256" / entry["manifest_digest"].removeprefix("sha256:")
        path.write_text('{"different":"content"}')
    elif mutation == "index":
        (tmp_path / "app/index.json").write_text('{"schemaVersion":2,"manifests":[]}')
    else:
        path = tmp_path / "app/blobs/sha256" / entry["layers"][0]["digest"].removeprefix("sha256:")
        if mutation == "layer_link":
            target = tmp_path / "target"
            path.rename(target)
            path.symlink_to(target)
        else:
            path.write_bytes(b"truncated")
    with pytest.raises(ValueError, match="digest|index|layer"):
        HELPERS["oci_inputs"](tmp_path, refs, revision)


def test_duplicate_evidence_json_keys_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.json"
    path.write_text('{"revision":"first","revision":"second"}')
    with pytest.raises(ValueError, match="Duplicate"):
        HELPERS["checked_json"](path)


def test_ci_flags_do_not_enable_image_scanning_on_the_mac(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setenv("WEATHER_CI_IN_CLUSTER", "1")
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "fixture.invalid")
    with pytest.raises(SystemExit, match="requires approved in-cluster"):
        HELPERS["main"]()
