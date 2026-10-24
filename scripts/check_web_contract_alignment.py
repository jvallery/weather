# SPDX-License-Identifier: MIT
"""Bounded pure ASGI→web-parser proof; no database, socket, browser or build.

The independent hand-typed boundary tests do not depend on this optional API integration
receipt. --api-source allows checking the exact isolated API source before it is merged.
"""

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-source", type=Path, default=Path.cwd())
    parser.add_argument(
        "--output", type=Path,
        default=Path("docs/operations/evidence/public-web-alignment-20261024.json"),
    )
    args = parser.parse_args()
    web = Path(__file__).resolve().parents[1]
    api = args.api_source.resolve()
    sys.path.insert(0, str(api / "app"))
    from fastapi.testclient import TestClient
    from weather.api.cursors import CursorCodec
    from weather.api.public_models import Current, History
    from weather.api.public_schema import contract_app
    from weather.api.public_service import CurrentReading, PublicWeatherService, SourcePage

    def sha(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def revision(path: Path) -> str:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=path, text=True).strip()

    fixtures = web / "web/app/fixtures"
    current_input = json.loads((fixtures / "current.json").read_text())
    current_input.pop("schema_version")  # Verify defaults are actually serialized.
    current = Current.model_validate(current_input)
    history_input = json.loads((fixtures / "history.json").read_text())
    history_input.pop("aggregation")
    history = History.model_validate(history_input)

    class Source:
        fail = False

        async def current(self):
            if self.fail:
                raise OSError("Synthetic source unavailable")
            return CurrentReading(current, current.metrics[0].observed_at)

        async def observations(self, query, snapshot):
            return SourcePage(history, "12", None)

        async def aggregates(self, query, snapshot):
            raise AssertionError("Not requested")

    clock = [datetime(2026, 11, 1, 8, 15, 5, tzinfo=UTC)]
    source = Source()
    service = PublicWeatherService(
        source, CursorCodec(bytes(range(32))), maximum_raw_seconds=31 * 86400,
        clock=lambda: clock[0],
        monotonic=lambda: (clock[0] - datetime(2026, 11, 1, 8, 15, 5, tzinfo=UTC)).total_seconds(),
    )
    with TestClient(contract_app(service)) as client:
        def read(path, params=None):
            response = client.get(path, params=params)
            response.raise_for_status()
            return response.json()

        current_payload = read("/api/v1/weather/current")
        source.fail = True
        clock[0] += timedelta(seconds=100)
        cached = read("/api/v1/weather/current")
        raw = read("/api/v1/weather/observations", {
            "start": "2026-11-01T06:00:00Z", "end": "2026-11-01T10:00:00Z",
            "metrics": "temperature",
        })
        registry = read("/api/v1/weather/metrics")
    if registry != json.loads((fixtures / "metric-registry.json").read_text()):
        raise SystemExit("The independent registry fixture has drifted from actual serialization")
    no_recipes = {**registry, "metrics": [
        {key: value for key, value in row.items() if key != "conversions"}
        for row in registry["metrics"]
    ]}
    cases = [
        ("current_defaults", "currentSchema", current_payload, True),
        ("cached_stale_source_times", "currentSchema", cached, True),
        ("raw_history_zero_resolution", "historySchema", raw, True),
        ("registry_with_conversions", "metricRegistrySchema", registry, True),
        ("reject_binned_raw_history", "historySchema", {**raw, "resolution_seconds": 60}, False),
        ("reject_registry_without_recipes", "metricRegistrySchema", no_recipes, False),
    ]
    payloads = [{"case": name, "schema": schema, "payload": value, "expected": expected}
                for name, schema, value, expected in cases]
    program = """import{pathToFileURL}from'node:url';
const schemas=await import(pathToFileURL(process.argv[1]));let input='';
for await(const chunk of process.stdin)input+=chunk;
process.stdout.write(JSON.stringify(JSON.parse(input).map(item=>{
const parsed=schemas[item.schema].safeParse(item.payload);
return{case:item.case,schema:item.schema,expected:item.expected,accepted:parsed.success};})));"""
    boundary = web / "web/app/lib/contracts.ts"
    result = subprocess.run(
        ["node", "--experimental-strip-types", "--input-type=module", "-e", program, str(boundary)],
        input=json.dumps(payloads), text=True, capture_output=True, check=True, timeout=15,
    )
    outcomes = json.loads(result.stdout)
    report = {
        "kind": "pure_serialization_boundary_audit", "serialized_via": "real_shared_router_asgi",
        "api_source": revision(api), "web_source": revision(web),
        "api_files_sha256": {name: sha(api / "app/weather/api" / name) for name in (
            "public_routes.py", "public_models.py", "public_service.py", "cursors.py",
            "metric_registry.py",
        )},
        "web_parser_sha256": sha(boundary), "script_sha256": sha(Path(__file__)),
        "fixture_sha256": {name: sha(fixtures / name) for name in ("current.json", "history.json")},
        "node_version": subprocess.check_output(["node", "--version"], text=True).strip(),
        "browser_or_build_executed": False,
        "cases": [{**outcome, "payload_sha256": hashlib.sha256(json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode()).hexdigest()}
                  for outcome, (_, _, value, _) in zip(outcomes, cases, strict=True)],
        "controls_are_not_production_payloads": True,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    if any(outcome["accepted"] != outcome["expected"] for outcome in outcomes):
        raise SystemExit("Serialized API boundary qualification failed; see receipt")
    print("Four real serialized API cases accepted; two malformed controls rejected")


if __name__ == "__main__":
    main()
