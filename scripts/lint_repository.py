# SPDX-License-Identifier: MIT
"""Cheap repository invariants; no image or media execution."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def has_spdx(file: Path) -> bool:
    pattern = r"^\s*(?:#|//|/\*|<!--|--)\s*SPDX-License-Identifier: MIT(?:\s*\*/|\s*-->)?\s*$"
    return any(re.fullmatch(pattern, line) for line in file.read_text().splitlines()[:5])


for directory in ("app", "scripts", "web"):
    for file in (ROOT / directory).rglob("*"):
        if any(
            part in {"node_modules", "build", ".react-router", "__pycache__"} for part in file.parts
        ):
            continue
        if not file.is_file():
            continue
        if file.name == "weather" or file.suffix in {
            ".py",
            ".sh",
            ".js",
            ".mjs",
            ".ts",
            ".tsx",
            ".jsx",
            ".css",
            ".sql",
            ".html",
        }:
            assert has_spdx(file), f"Missing SPDX header: {file}"
for name in ("Dockerfile.app", "Dockerfile.web", "Dockerfile.ci"):
    body = (ROOT / name).read_text()
    for image in re.findall(r"^FROM (\S+)", body, re.M):
        assert re.fullmatch(r"registry\.vallery\.net/.+@sha256:[0-9a-f]{64}", image), image
    users = re.findall(r"^USER (.+)$", body, re.M)
    assert users and re.fullmatch(r"[1-9][0-9]*:[1-9][0-9]*", users[-1]), name
    assert has_spdx(ROOT / name), f"Missing Dockerfile SPDX: {name}"
body = (ROOT / "Dockerfile.app").read_text()
assert "ffmpeg-9.0.2.tar.xz" in body
assert "8c3850283eb25fa026482078a04051e0be17347b09ef81a0849bec15a96e002e" in body
assert "sha256sum -c" in body
print("SPDX, mirror digest, non-root and FFmpeg checksum checks passed")

for source_import in (ROOT / "images/upstream-imports").rglob("Dockerfile"):
    assert has_spdx(source_import), f"Missing import SPDX: {source_import}"
