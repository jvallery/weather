# SPDX-License-Identifier: MIT
"""Bounded read-only security smoke; receipts contain no response bodies or private data."""

import argparse
import json
import re
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

ORIGIN = "https://weather.vallery.net"
MAX_BYTES = 2 * 1024 * 1024
PROTECTED_HEADERS = (
    "Content-Security-Policy",
    "Strict-Transport-Security",
    "X-Content-Type-Options",
    "Referrer-Policy",
)
PRIVATE_MARKERS = re.compile(
    r'(?i)(?:"(?:serial|serial_number|hub_sn|device_sn|latitude|longitude|camera_ip|'
    r'camera_address)"\s*:|\b(?:\d{1,3}\.){3}\d{1,3}\b|'
    r"\b[\w.-]+\.svc(?:\.cluster\.local)?\b|Traceback \(most recent call last\))"
)


def csp_errors(policies: list[str]) -> list[str]:
    errors = []
    if not policies:
        return ["missing CSP"]
    for policy in policies:
        directives = {}
        for item in policy.split(";"):
            tokens = item.split()
            if tokens and tokens[0].lower() not in directives:
                directives[tokens[0].lower()] = tokens[1:]
        if [token.lower() for token in directives.get("frame-ancestors", [])] != ["'none'"]:
            errors.append("framing is not denied")
        for name in ("script-src", "script-src-elem"):
            effective = directives.get(
                name, directives.get("script-src", directives.get("default-src"))
            )
            if effective is None or "'unsafe-inline'" in {token.lower() for token in effective}:
                errors.append("script policy allows unrestricted inline scripts")
                break
    return errors


def probe(path: str, expected: int, *, require_headers: bool) -> dict[str, object]:
    request = urllib.request.Request(
        ORIGIN + path,
        headers={
            "User-Agent": "weather-launch-verifier/1.0",
            "Accept": "text/html,application/json",
        },
    )
    try:
        response = urllib.request.urlopen(request, timeout=15)
    except urllib.error.HTTPError as error:
        response = error
    failures = []
    with response:
        body = response.read(MAX_BYTES + 1)
        if response.geturl() != ORIGIN + path:
            failures.append("unexpected redirect")
        if response.status != expected:
            failures.append("unexpected status")
        if len(body) > MAX_BYTES:
            failures.append("response exceeded inspection budget")
        if PRIVATE_MARKERS.search(body.decode("utf8", errors="replace")):
            failures.append("private metadata or stack trace pattern")
        presence = {name: bool(response.headers.get(name)) for name in PROTECTED_HEADERS}
        if require_headers:
            failures.extend(csp_errors(response.headers.get_all("Content-Security-Policy", [])))
            if response.headers.get("X-Content-Type-Options", "").lower() != "nosniff":
                failures.append("missing nosniff")
            if response.headers.get("Referrer-Policy", "").lower() not in {
                "no-referrer",
                "same-origin",
                "strict-origin",
                "strict-origin-when-cross-origin",
            }:
                failures.append("missing bounded referrer policy")
            hsts = response.headers.get("Strict-Transport-Security", "")
            if re.search(r"(?i)(?:^|;)\s*max-age=([1-9]\d*)(?:;|$)", hsts) is None:
                failures.append("missing HSTS")
        return {
            "path": path,
            "status": response.status,
            "headers_present": presence,
            "inspected_bytes": min(len(body), MAX_BYTES),
            "failures": failures,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    results = [
        probe(path, expected, require_headers=headers)
        for path, expected, headers in (
            ("/", 200, True),
            ("/weather/history/", 200, True),
            ("/weather-launch-nonexistent", 404, True),
            ("/editor", 404, False),
            ("/api/v1/operator/x", 404, False),
            ("/metrics", 404, False),
        )
    ]
    receipt = {
        "schema": "weather-public-launch-smoke/v1",
        "observed_at": datetime.now(UTC).isoformat(),
        "origin": ORIGIN,
        "results": results,
        "passed": all(not item["failures"] for item in results),
        "limits": [
            "No browser/accessibility or telemetry/recovery acceptance claimed.",
            "Pattern inspection complements the typed public API allowlist; "
            "it is not an exhaustive proof that arbitrary text contains no private data.",
        ],
    }
    args.output.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))
    if not receipt["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
