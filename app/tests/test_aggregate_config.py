# SPDX-License-Identifier: MIT
import json
from fractions import Fraction
from pathlib import Path

import pytest
from weather.api.aggregate_config import (
    AggregateConfigurationError,
    load_aggregate_configuration,
    support_binding,
)


def test_support_file_retains_exact_calibration_and_shared_worker_binding(tmp_path: Path):
    path = tmp_path / "policy.json"
    path.write_text(
        '{"version":1,"metrics":{"temperature":{"mode":"forward_hold",'
        '"revision":"calibrated_fixture_v1","hold_seconds":60.000001}},'
        '"pressure":{"endpoint_seconds":300,"minimum_fraction":0.9}}'
    )
    config = load_aggregate_configuration(path)
    assert config.policies["temperature"].hold_seconds == Fraction(60000001, 1000000)
    assert config.pressure_minimum_fraction == Fraction(9, 10)
    assert config.document == support_binding(config.policies, 300, Fraction(9, 10))
    original = config.revision
    path.write_text(path.read_text().replace("60.000001", "60.000002"))
    assert load_aggregate_configuration(path).revision != original


@pytest.mark.parametrize(
    "document",
    [
        '{"version":1,"version":1,"metrics":{}}',
        '{"version":true,"metrics":{}}',
        '{"version":1,"metrics":{"temperature":{"mode":"forward_hold",'
        '"revision":"fixture","hold_seconds":true}}}',
        '{"version":1,"metrics":{"lightning_count":{"mode":"forward_hold",'
        '"revision":"fixture","hold_seconds":60}}}',
        '{"version":1,"metrics":{"precipitation":{"mode":"reported_interval",'
        '"revision":"fixture","reported_policies":["unknown"],'
        '"maximum_reported_seconds":60,"authority":"obs_st"}}}',
        '{"version":1,"metrics":{},"private_fixture":"not-a-support-policy"}',
    ],
)
def test_unqualified_or_extra_support_configuration_is_value_free(tmp_path: Path, document: str):
    path = tmp_path / "policy.json"
    path.write_text(document)
    with pytest.raises(AggregateConfigurationError) as error:
        load_aggregate_configuration(path)
    assert str(error.value) == "Invalid aggregate support policy file"


def test_empty_support_file_makes_no_authority_or_duration_claim(tmp_path: Path):
    path = tmp_path / "policy.json"
    path.write_text(json.dumps({"version": 1, "metrics": {}}))
    config = load_aggregate_configuration(path)
    assert not config.policies
    assert config.pressure_endpoint_seconds is None
    assert config.pressure_minimum_fraction is None
