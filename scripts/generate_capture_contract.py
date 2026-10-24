# SPDX-License-Identifier: MIT
"""Render the private code-first v2 capture manifest schema; no media work."""

import argparse
import json
from pathlib import Path

from weather.capture.records import ADAPTER


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    target = Path(__file__).resolve().parents[1] / "docs/contracts/capture.schema.json"
    schema = ADAPTER.json_schema()
    schema.update(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "https://weather.vallery.net/contracts/capture-v2.schema.json",
            "title": "Private single-USTAR capture manifest v2",
        }
    )
    content = json.dumps(schema, indent=2, sort_keys=True) + "\n"
    if args.check:
        if target.read_text() != content:
            raise SystemExit("Capture schema differs from code; regenerate it")
    else:
        target.write_text(content)


if __name__ == "__main__":
    main()
