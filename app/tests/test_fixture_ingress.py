# SPDX-License-Identifier: MIT
"""#21: the e2e fixture ingress applies exactly deploy/k8s's routes and Middlewares."""

import json
import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_the_fixture_ingress_table_equals_what_deploy_k8s_renders() -> None:
    module = runpy.run_path(str(ROOT / "scripts/fixture_ingress.py"))
    rendered = module["render"]()
    assert json.loads((ROOT / "web/e2e/fixture-ingress.json").read_text()) == rendered
    names = {name for route in rendered["routes"] for name in route["middlewares"]}
    assert names == set(rendered["middlewares"])
    # Every public route carries the security headers; only hashed assets skip the rate limit.
    assert all("weather-security-headers" in route["middlewares"] for route in rendered["routes"])
    unlimited = [
        route["match"]
        for route in rendered["routes"]
        if not any("rate-limit" in name for name in route["middlewares"])
    ]
    assets = "Host(`weather.vallery.net`) && (PathPrefix(`/assets/`) || PathPrefix(`/fonts/`))"
    assert unlimited == [assets]
    assert rendered["excluded"] == [
        "network-infra/ak-forwardauth",
        "network-infra/apps-strip-auth-headers",
    ]
