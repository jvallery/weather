# SPDX-License-Identifier: MIT
import runpy
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
