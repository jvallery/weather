# SPDX-License-Identifier: MIT
"""Generate or check Pydantic -> OpenAPI -> TypeScript (P12). No service or network access."""

import argparse
import json
import subprocess
from pathlib import Path

from weather.api.routes import public_openapi

ROOT = Path(__file__).resolve().parents[1]
OPENAPI = Path("docs/contracts/weather.openapi.json")
TYPESCRIPT = Path("web/app/generated/weather-api.ts")


def rendered() -> dict[Path, str]:
    schema = json.dumps(public_openapi(), sort_keys=True, indent=2) + "\n"
    result = subprocess.run(
        ["node", str(ROOT / "web/contract-tools/generate.mjs")],
        cwd=ROOT,
        input=schema,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return {OPENAPI: schema, TYPESCRIPT: result.stdout}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    results = rendered()
    if args.write:
        for relative, content in results.items():
            (ROOT / relative).parent.mkdir(parents=True, exist_ok=True)
            (ROOT / relative).write_text(content)
        print("Generated OpenAPI and TypeScript from the Pydantic models")
        return
    drift = [
        str(relative)
        for relative, content in results.items()
        if not (ROOT / relative).exists() or (ROOT / relative).read_text() != content
    ]
    if drift:
        raise SystemExit(
            "API contract drift in " + ", ".join(drift) + "; run scripts/ci.sh contracts --write"
        )
    print("OpenAPI and TypeScript match the Pydantic models")


if __name__ == "__main__":
    main()
