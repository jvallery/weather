# SPDX-License-Identifier: MIT
"""Render web/e2e/fixture-ingress.json from deploy/k8s (#21): the CI fixture ingress applies the
same routes, priorities and middlewares as production. `--write` regenerates; a unit test fails
when the file and the manifests differ.

Cross-namespace middlewares (network-infra's auth-header strip and Authentik forward-auth on the
editor routes) cannot run in the fixture; they are listed under `excluded`, never silently dropped.
"""

import json
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
SOURCES = (
    "deploy/k8s/ingress.yaml",
    "deploy/k8s/search/ingress.yaml",
    "deploy/k8s/editor-ingress.yaml",
)
TARGET = ROOT / "web/e2e/fixture-ingress.json"
UPSTREAMS = {"api": "api", "api-operator": "api", "weather-preview": "api", "web": "web"}


def render() -> dict[str, Any]:
    middlewares: dict[str, Any] = {}
    routes: list[dict[str, Any]] = []
    excluded: set[str] = set()
    for source in SOURCES:
        for doc in yaml.safe_load_all((ROOT / source).read_text()):
            if not doc:
                continue
            if doc["kind"] == "Middleware":
                spec = doc["spec"]
                if "rateLimit" in spec:
                    limit = spec["rateLimit"]
                    middlewares[doc["metadata"]["name"]] = {
                        "rateLimit": {
                            "average": limit["average"],
                            "period": limit.get("period", "1s"),
                            "burst": limit["burst"],
                            "header": limit["sourceCriterion"]["requestHeaderName"],
                        }
                    }
                elif "inFlightReq" in spec:
                    flight = spec["inFlightReq"]
                    middlewares[doc["metadata"]["name"]] = {
                        "inFlightReq": {
                            "amount": flight["amount"],
                            "header": flight["sourceCriterion"]["requestHeaderName"],
                        }
                    }
                elif "headers" in spec:
                    middlewares[doc["metadata"]["name"]] = {"headers": spec["headers"]}
            elif doc["kind"] == "IngressRoute":
                for route in doc["spec"]["routes"]:
                    names = []
                    for item in route.get("middlewares", []):
                        if item.get("namespace", "weather") != "weather":
                            excluded.add(f"{item['namespace']}/{item['name']}")
                        else:
                            names.append(item["name"])
                    (service,) = route["services"]
                    routes.append(
                        {
                            "match": route["match"],
                            "priority": route.get("priority", len(route["match"])),
                            "middlewares": names,
                            "upstream": UPSTREAMS[service["name"]],
                        }
                    )
    routes.sort(key=lambda r: (-r["priority"], -len(r["match"])))
    return {"middlewares": middlewares, "routes": routes, "excluded": sorted(excluded)}


def main() -> None:
    text = json.dumps(render(), indent=1, sort_keys=True) + "\n"
    if "--write" in sys.argv[1:]:
        TARGET.write_text(text)
    elif TARGET.read_text() != text:
        raise SystemExit("web/e2e/fixture-ingress.json differs from deploy/k8s; run --write")


if __name__ == "__main__":
    main()
