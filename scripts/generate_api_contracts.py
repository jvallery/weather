# SPDX-License-Identifier: MIT
"""Generate or compare Pydantic -> OpenAPI -> TypeScript without service/network access."""

import argparse
import json
import subprocess
from pathlib import Path

from weather.api.public_schema import public_openapi

ROOT = Path(__file__).resolve().parents[1]
OPENAPI = Path("docs/contracts/weather.openapi.json")
TYPESCRIPT = Path("web/app/generated/weather-api.ts")


def rendered() -> dict[Path, str]:
    schema = json.dumps(public_openapi(), sort_keys=True, indent=2) + "\n"
    result = subprocess.run(
        ["pnpm", "--dir", "web/contract-tools", "exec", "node", "generate.mjs"],
        cwd=ROOT,
        input=schema,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    if len(result.stdout.encode()) > 2 * 1024 * 1024:
        raise ValueError("Generated TypeScript exceeds the contract budget")
    return {OPENAPI: schema, TYPESCRIPT: result.stdout}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    results = rendered()
    if args.write:
        for relative, content in results.items():
            target = ROOT / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        print("Generated public OpenAPI and TypeScript from Pydantic")
    else:
        drift = [
            str(relative)
            for relative, content in results.items()
            if not (ROOT / relative).exists() or (ROOT / relative).read_text() != content
        ]
        if drift:
            raise SystemExit(
                "API contract drift: " + ", ".join(drift) + "; run scripts/ci.sh contracts --write"
            )
        print("Code-first OpenAPI and TypeScript are unchanged")


if __name__ == "__main__":
    main()
