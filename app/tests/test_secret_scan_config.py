# SPDX-License-Identifier: MIT
"""The repo gitleaks config skips only the CI-generated root trees (weather#273).

A planted fake token in tracked source, or in a nested directory that merely shares a
generated tree's name, must still fail the scan; the same token under the root .venv/ (and
under node_modules/, which gitleaks' default config skips) must not. The token is joined at
runtime, never folded into a constant, so neither this file nor its bytecode matches.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
# github-pat shape, not a real token
FAKE_TOKEN = "".join(["ghp", "_", *(["A1b2C3d4"] * 4), "E5f6"])


def _scan(tree: Path) -> subprocess.CompletedProcess[str]:
    gitleaks = shutil.which("gitleaks")
    if gitleaks is None:
        if os.environ.get("WEATHER_CI_IN_CLUSTER") == "1":
            pytest.fail("gitleaks must be on PATH in fleet CI")
        pytest.skip("gitleaks is not installed")
    shutil.copy(ROOT / ".gitleaks.toml", tree / ".gitleaks.toml")
    return subprocess.run(
        [
            gitleaks,
            "dir",
            ".",
            "--config",
            ".gitleaks.toml",
            "--redact",
            "--no-banner",
            "--exit-code",
            "1",
        ],
        cwd=tree,
        capture_output=True,
        text=True,
        check=False,
    )


def _plant(tree: Path, relative: str) -> None:
    path = tree / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'TOKEN = "{FAKE_TOKEN}"\n')


@pytest.mark.parametrize(
    "relative", ["app/planted.py", "web/app/planted.ts", "app/.venv/planted.py"]
)
def test_planted_secret_in_scanned_path_fails(tmp_path: Path, relative: str) -> None:
    _plant(tmp_path, relative)
    assert _scan(tmp_path).returncode == 1


@pytest.mark.parametrize(
    "relative",
    [
        ".venv/lib/planted.py",
        "web/node_modules/pkg/planted.js",
        "web/contract-tools/node_modules/pkg/planted.js",
    ],
)
def test_generated_root_trees_are_skipped(tmp_path: Path, relative: str) -> None:
    _plant(tmp_path, relative)
    assert _scan(tmp_path).returncode == 0
