# SPDX-License-Identifier: MIT
"""Persistence identity/exact-value preparation, with no connection or database."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from fractions import Fraction

import pytest
from weather.api.aggregate_adapter import compute
from weather.db.aggregate_store import prepare
from weather.products.aggregate import Interval, Support

START = datetime(2026, 1, 1, tzinfo=UTC)


def computation():
    window = Interval(START, START + timedelta(seconds=60))
    return compute(
        window,
        "temperature",
        "mean",
        {
            "temperature": [Support(window, Fraction(1, 3), "synthetic-evidence")],
        },
        evidence_revision="synthetic_revision",
    )


def test_store_identity_binds_station_scope_policy_and_exact_values():
    value = computation()
    first = prepare("Fixture A", value, boundary="1", policy_revision="policy_1")
    verified_later = prepare("Fixture A", value, boundary="2", policy_revision="policy_1")
    other_station = prepare("Fixture B", value, boundary="1", policy_revision="policy_1")
    new_policy = prepare("Fixture A", value, boundary="1", policy_revision="policy_2")
    assert first.id == verified_later.id != other_station.id
    assert first.id != new_policy.id
    assert first.parameters["numerator"] == "1"
    assert first.parameters["denominator"] == "3"
    assert json.loads(first.parameters["document"])["value"] == pytest.approx(1 / 3)


def test_no_public_projection_can_overwrite_its_exact_evidence():
    value = computation()
    wrong = replace(value, aggregate=value.aggregate.model_copy(update={"value": 0}))
    with pytest.raises(ValueError, match="projection"):
        prepare("Fixture", wrong, boundary="1", policy_revision="policy_1")


@pytest.mark.parametrize(
    "station,boundary,policy",
    [
        ("private\nfixture", "1", "policy_1"),
        ("Fixture", "01", "policy_1"),
        ("Fixture", "1", "invalid policy"),
    ],
)
def test_invalid_persistence_coordinates_are_rejected_without_values(station, boundary, policy):
    with pytest.raises(ValueError):
        prepare(station, computation(), boundary=boundary, policy_revision=policy)
