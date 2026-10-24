# SPDX-License-Identifier: MIT
import subprocess


def test_cli_help() -> None:
    result = subprocess.run(["weather", "--help"], capture_output=True, text=True, check=True)
    assert "Weather archive services" in result.stdout
