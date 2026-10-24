# SPDX-License-Identifier: MIT
import runpy
from pathlib import Path

import pytest

HELPERS = runpy.run_path(
    str(Path(__file__).resolve().parents[2] / "scripts/verify_public_launch.py")
)


@pytest.mark.parametrize(
    "policy",
    [
        "default-src 'self'; frame-ancestors 'none'",
        "script-src 'nonce-fixture'; frame-ancestors 'none'",
        "script-src; frame-ancestors 'none'",
        "default-src 'none'; frame-ancestors 'none'",
    ],
)
def test_bounded_script_and_frame_policies_pass(policy: str) -> None:
    assert HELPERS["csp_errors"]([policy]) == []


@pytest.mark.parametrize(
    "policies",
    [
        [],
        ["frame-ancestors 'none'"],
        ["default-src 'self'"],
        ["default-src 'self'; script-src-elem 'unsafe-inline'; frame-ancestors 'none'"],
        ["script-src 'UNSAFE-INLINE'; frame-ancestors 'none'"],
        ["default-src 'self'; frame-ancestors https://unrelated.invalid"],
    ],
)
def test_missing_or_unrestricted_launch_policies_fail(policies: list[str]) -> None:
    assert HELPERS["csp_errors"](policies)


def test_sensitive_patterns_do_not_treat_timestamps_or_measurements_as_addresses() -> None:
    marker = HELPERS["PRIVATE_MARKERS"]
    assert not marker.search('{"observed_at":"2026-01-01T00:00:00Z","value":0.0}')
    assert marker.search('{"serial_number":"synthetic-fixture"}')
    assert marker.search('{"latitude":12.5}')
    assert marker.search("http://example.svc.cluster.local/")
