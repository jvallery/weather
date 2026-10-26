# SPDX-License-Identifier: MIT
"""#148: Traefik rate limits are per replica; the manifests divide the intended per-visitor
limit by the traefik-apps DaemonSet size (one pod per worker)."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2] / "deploy" / "k8s"
TRAEFIK_REPLICAS = 5  # traefik-apps DaemonSet, one pod on each of the 5 workers


def middleware(path: Path, name: str) -> dict:
    documents = yaml.safe_load_all(path.read_text())
    return next(d for d in documents if d["kind"] == "Middleware" and d["metadata"]["name"] == name)


def test_site_rate_limit_is_30_per_second_burst_60_per_visitor() -> None:
    limit = middleware(ROOT / "ingress.yaml", "weather-rate-limit")["spec"]["rateLimit"]
    assert limit["average"] * TRAEFIK_REPLICAS == 30
    assert limit["burst"] * TRAEFIK_REPLICAS == 60
    assert limit["sourceCriterion"] == {"requestHeaderName": "Cf-Connecting-Ip"}


def test_search_rate_limit_is_30_per_minute_burst_10_per_visitor() -> None:
    spec = middleware(ROOT / "search" / "ingress.yaml", "weather-search-rate-limit")["spec"]
    limit = spec["rateLimit"]
    assert limit["period"] == "1m"
    assert limit["average"] * TRAEFIK_REPLICAS == 30
    assert limit["burst"] * TRAEFIK_REPLICAS == 10
