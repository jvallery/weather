# SPDX-License-Identifier: MIT
"""Check the exact approved font bytes and distribution notices without rendering."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for package in json.loads((ROOT / "docs/research/font-provenance.json").read_text())[
    "packages"
]:
    filename = Path(package["font_member"]).name
    font = ROOT / "web/public/fonts" / filename
    notice = (
        ROOT
        / "web/public/fonts"
        / ("OFL-sans.txt" if "sans" in package["name"] else "OFL-mono.txt")
    )
    if hashlib.sha256(font.read_bytes()).hexdigest() != package["font_sha256"]:
        raise SystemExit(f"Font differs from approved provenance: {filename}")
    if hashlib.sha256(notice.read_bytes()).hexdigest() != package["license_sha256"]:
        raise SystemExit(f"Font distribution notice differs: {notice.name}")
print("Both Fontsource 5.3.0 font and OFL notice hashes match the approved provenance")
