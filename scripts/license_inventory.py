# SPDX-License-Identifier: MIT
"""Check an inventory covering every version in both lockfiles, including optional platforms.

Regenerate with --update after reviewing changed upstream license metadata.
--manifest checks a standalone synthetic license policy fixture.
"""

import argparse
import json
import re
import tomllib
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
ALLOW = {
    "MIT",
    "ISC",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "Apache-2.0",
    "PSF-2.0",
    "Python-2.0",
    "Unlicense",
    "CC0-1.0",
    "CC-BY-4.0",
    "BlueOak-1.0.0",
    "MPL-2.0",
    "0BSD",
}
CLASSIFIERS = {
    "Apache Software License": "Apache-2.0",
    "MIT License": "MIT",
    "Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
    "BSD License": "BSD-3-Clause",
    "Python Software Foundation License": "PSF-2.0",
}


def closure() -> set[tuple[str, str, str]]:
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    result = {
        ("pypi", x["name"], x["version"])
        for x in lock["package"]
        if "registry" in x.get("source", {})
    }
    for doc in yaml.safe_load_all((ROOT / "web/pnpm-lock.yaml").read_text()):
        for key in doc.get("packages", {}):
            name, version = key.rsplit("@", 1)
            result.add(("npm", name, version))
    return result


def metadata(item: tuple[str, str, str]) -> dict[str, str]:
    ecosystem, name, version = item
    url = (
        f"https://pypi.org/pypi/{name}/{version}/json"
        if ecosystem == "pypi"
        else f"https://registry.npmjs.org/{urllib.parse.quote(name, safe='')}/{version}"
    )
    with urllib.request.urlopen(url, timeout=30) as response:
        data = json.load(response)
    if ecosystem == "pypi":
        info = data["info"]
        license_id = info.get("license_expression") or info.get("license") or ""
        if not license_id or len(license_id) > 150:
            license_id = next(
                (
                    CLASSIFIERS[c.removeprefix("License :: OSI Approved :: ")]
                    for c in info.get("classifiers", [])
                    if c.removeprefix("License :: OSI Approved :: ") in CLASSIFIERS
                ),
                "UNKNOWN",
            )
    else:
        license_id = data.get("license", "UNKNOWN")
        if isinstance(license_id, dict):
            license_id = license_id.get("type", "UNKNOWN")
    # This release omits license metadata; its wheel LICENSE was reviewed by hash.
    reviewed_mit = {("mypy-extensions", "1.1.0"), ("hatchling", "1.29.0")}
    if ecosystem == "pypi" and (name, version) in reviewed_mit:
        license_id = "MIT"
    return dict(ecosystem=ecosystem, name=name, version=version, license=license_id, source=url)


def check(rows: list[dict[str, str]]) -> None:
    bad = []
    for row in rows:
        terms = set(re.findall(r"[A-Za-z0-9][A-Za-z0-9.+-]*", row["license"])) - {"OR", "AND"}
        if not terms or not terms <= ALLOW:
            bad.append(f"{row['name']}@{row['version']}: {row['license']}")
    if bad:
        raise SystemExit("Disallowed or unknown licenses:\n" + "\n".join(bad))
    print(f"License allowlist passed: {len(rows)} locked package versions")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--update", action="store_true")
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    target = ROOT / "supply-chain/licenses.json"
    expected = closure() if not args.manifest else None
    if args.update:
        previous = json.loads(target.read_text()) if target.exists() else []
        cached = {
            (x["ecosystem"], x["name"], x["version"]): x
            for x in previous
            if x.get("license") != "UNKNOWN"
        }
        pending = sorted((expected or set()) - set(cached))
        with ThreadPoolExecutor(max_workers=6) as pool:
            fetched = list(pool.map(metadata, pending))
        cached.update({(x["ecosystem"], x["name"], x["version"]): x for x in fetched})
        rows = [cached[key] for key in sorted(expected or set())]
        target.parent.mkdir(exist_ok=True)
        target.write_text(json.dumps(rows, indent=2) + "\n")
    rows = json.loads((args.manifest or target).read_text())
    if expected is not None:
        actual = {(x["ecosystem"], x["name"], x["version"]) for x in rows}
        if actual != expected:
            raise SystemExit("License inventory does not match lockfiles; review and run --update")
    check(rows)


if __name__ == "__main__":
    main()
