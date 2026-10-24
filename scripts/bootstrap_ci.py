# SPDX-License-Identifier: MIT
"""Install checksum-pinned Linux amd64 CI tools into disposable pod storage."""

import hashlib
import io
import os
import platform
import tarfile
import urllib.request
import zipfile
from pathlib import Path

if platform.system() != "Linux" or platform.machine() not in {"x86_64", "amd64"}:
    raise SystemExit("CI bootstrap requires the approved Linux amd64 fleet runner")
root = Path(os.environ.get("WEATHER_CI_TOOLS", "/tmp/weather-ci-tools"))
root.mkdir(parents=True, exist_ok=True)


def download(url: str, expected: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != expected:
        raise SystemExit("CI tool archive checksum mismatch")
    return data


if not (root / "uv").exists():
    archive = zipfile.ZipFile(
        io.BytesIO(
            download(
                "https://files.pythonhosted.org/packages/3a/ad/3c2a81d66d1839063662e5b6c0002ec31f8ee987dbc5295747627ca6160a/uv-0.13.5-py3-none-manylinux_2_17_x86_64.manylinux2014_x86_64.whl",
                "fa8d26e08e34c29c0c4e820cf3181757086d547240b4f4d31ff1d8bd810ca946",
            )
        )
    )
    member = next(x for x in archive.namelist() if x.endswith("/scripts/uv"))
    (root / "uv").write_bytes(archive.read(member))
    (root / "uv").chmod(0o755)
if not (root / "node-v24.22.1-linux-x64/bin/node").exists():
    archive = tarfile.open(
        fileobj=io.BytesIO(
            download(
                "https://nodejs.org/dist/v24.22.1/node-v24.22.1-linux-x64.tar.xz",
                "6c172a9ab6718f7b801fb5a7bd486af2ee5e32758086a04b84fcce2aa77095e6",
            )
        )
    )
    archive.extractall(root, filter="data")
if not (root / "gitleaks").exists():
    archive = tarfile.open(
        fileobj=io.BytesIO(
            download(
                "https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_linux_x64.tar.gz",
                "551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb",
            )
        )
    )
    member = archive.extractfile("gitleaks")
    assert member is not None
    (root / "gitleaks").write_bytes(member.read())
    (root / "gitleaks").chmod(0o755)

if not (root / "syft").exists():
    archive = tarfile.open(
        fileobj=io.BytesIO(
            download(
                "https://github.com/anchore/syft/releases/download/v1.55.0/syft_1.55.0_linux_amd64.tar.gz",
                "bb65249542db5fedeeb4d3f9b018b75e7264dbfbbb10486f044581a92f90490d",
            )
        )
    )
    member = archive.extractfile("syft")
    assert member is not None
    (root / "syft").write_bytes(member.read())
    (root / "syft").chmod(0o755)
