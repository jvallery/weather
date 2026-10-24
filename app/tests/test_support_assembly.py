# SPDX-License-Identifier: MIT
"""Complete synthetic source evidence, never SQL, raw pages or inferred report spans."""

import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from uuid import NAMESPACE_DNS, uuid5

import pytest
from weather.api.public_models import HistoryPoint, Interval
from weather.api.support_assembly import (
    SupportPolicy,
    assemble,
    compute_window,
    required_read_spans,
)
from weather.db.public_evidence import EvidenceWindow, SupportEvidence
from weather.products.aggregate import Interval as Window

START = datetime(2026, 1, 1, tzinfo=UTC)
WIND_REPORT = "v171-wind-report-interval-1"
RAIN_REPORT = "synthetic_qualified_rain_v1"
COUNT_REPORT = "synthetic_qualified_count_v1"


def at(seconds: int) -> datetime:
    return START + timedelta(seconds=seconds)


def window(start: int = 0, end: int = 60) -> Window:
    return Window(at(start), at(end))


def hold(
    seconds: int | Fraction = 60, revision: str = "synthetic_calibrated_hold_v1"
) -> SupportPolicy:
    return SupportPolicy("forward_hold", revision, hold_seconds=seconds)


def report(policy: str, authority: str | None = None, maximum: int = 60) -> SupportPolicy:
    return SupportPolicy(
        "reported_interval",
        "synthetic_report_recipe_v1",
        reported_policies=(policy,),
        maximum_reported_seconds=maximum,
        authority=authority,
    )


def sample(
    value: str | int | None,
    seconds: int,
    identity: str,
    *,
    metric: str = "temperature",
    device: str = "synthetic_device",
    family: str = "obs_st",
    source_eligible: bool = True,
    kind: str = "instantaneous",
    policy: str = "requires-source-time-hold-policy",
    qualified: bool = False,
    reported_seconds: int | None = None,
) -> SupportEvidence:
    from weather.api.metric_registry import metric_definition

    exact = Decimal(value) if value is not None else None
    point = HistoryPoint(
        observed_at=at(seconds),
        received_at=at(seconds + 1),
        metric=metric,
        value=None
        if exact is None
        else int(exact)
        if metric == "lightning_count"
        else float(exact),
        unit=metric_definition(metric).canonical_unit,
        quality="valid" if exact is not None else "missing",
        evidence_id=identity,
        reason=None if exact is not None else "Synthetic null evidence",
        support_interval=Interval(start=at(seconds - reported_seconds), end=at(seconds))
        if qualified and reported_seconds is not None
        else None,
    )
    # Pydantic requires integer counts without any float intermediate.
    if metric == "lightning_count" and exact is not None:
        point = HistoryPoint.model_validate({**point.model_dump(), "value": int(exact)})
    return SupportEvidence(
        point=point,
        exact_value=exact,
        observation_id=uuid5(NAMESPACE_DNS, identity),
        family=family,
        device_alias=device,
        publication_seq=1,
        source_time_eligible=source_eligible,
        support_kind=kind,
        support_policy=policy,
        support_qualified=qualified,
        reported_seconds=reported_seconds,
        quality_revision="quality_v1_" + hashlib.sha256(identity.encode()).hexdigest(),
    )


def inputs(*rows: SupportEvidence, watermark: str = "9") -> EvidenceWindow:
    return EvidenceWindow(watermark, rows)


def test_complete_bookends_break_at_invalid_source_boundary_and_stop_at_metric_cap() -> None:
    evidence = inputs(
        sample(2, -10, "preceding"), sample(None, 10, "invalid"), sample(4, 20, "later")
    )
    result = compute_window(
        assemble(window(), evidence, {"temperature": hold(30)}), "temperature", "mean"
    )
    assert result.exact.value == Fraction(7, 2)
    assert result.aggregate.coverage.support_seconds == 40
    assert result.aggregate.coverage.fraction == pytest.approx(Fraction(2, 3))
    assert [(gap.start, gap.end) for gap in result.aggregate.coverage.gaps] == [
        (at(10), at(20)),
        (at(50), at(60)),
    ]


def test_same_timestamp_competing_and_invalid_ties_are_not_uuid_winners() -> None:
    policies = {"temperature": hold()}
    for other in (sample(3, 0, "competing"), sample(None, 0, "invalid_tie")):
        assembled = assemble(window(), inputs(sample(2, 0, "first"), other), policies)
        result = compute_window(assembled, "temperature", "mean")
        assert result.aggregate.value is None
        assert result.aggregate.coverage.fraction == 0
        assert result.aggregate.evidence_ids == tuple(sorted(("first", other.point.evidence_id)))


def test_equal_ties_and_separate_streams_do_not_double_count_duration() -> None:
    a, b = sample(2, 0, "first"), sample(2, 0, "other", device="second_device")
    assembled = assemble(window(), inputs(b, a), {"temperature": hold()})
    assert len(assembled.supports["temperature"]) == 2
    result = compute_window(assembled, "temperature", "mean")
    assert result.aggregate.value == 2 and result.aggregate.coverage.support_seconds == 60
    # A next sample only truncates its own device/family stream; the independent
    # remaining stream still overlaps and conflicts rather than winning by order.
    changed = assemble(window(), inputs(a, b, sample(4, 30, "next")), {"temperature": hold()})
    assert compute_window(changed, "temperature", "mean").aggregate.coverage.support_seconds == 30


def test_source_time_ineligible_rows_never_create_or_break_holds() -> None:
    a = sample(2, 0, "first")
    bad = sample(None, 10, "bad_clock", source_eligible=False)
    assembled = assemble(window(), inputs(a, bad), {"temperature": hold(30)})
    result = compute_window(assembled, "temperature", "mean")
    assert result.aggregate.value == 2 and result.aggregate.coverage.support_seconds == 30
    assert (
        assembled.evidence_revision
        != assemble(
            window(),
            inputs(a),
            {"temperature": hold(30)},
        ).evidence_revision
    )


def test_decimal_precision_survives_public_float_projection_for_downstream_ranking() -> None:
    a = sample("1.000000000000000001", 0, "low")
    b = sample("1.000000000000000002", 30, "high")
    assert a.point.value == b.point.value
    assembled = assemble(window(), inputs(a, b), {"temperature": hold(30)})
    result = compute_window(assembled, "temperature", "max")
    assert result.exact.value == Fraction(1000000000000000002, 10**18)
    assert result.aggregate.value == 1


def test_qualified_reported_wind_uses_actual_preceding_support_without_hold_fallback() -> None:
    row = sample(
        4,
        60,
        "wind",
        metric="wind_avg",
        kind="reported_interval",
        policy=WIND_REPORT,
        qualified=True,
        reported_seconds=60,
    )
    assembled = assemble(window(), inputs(row), {"wind_avg": report(WIND_REPORT)})
    result = compute_window(assembled, "wind_avg", "mean")
    assert result.aggregate.value == 4 and result.aggregate.coverage.fraction == 1
    unsupported = assemble(
        window(), inputs(replace(row, support_qualified=False)), {"wind_avg": report(WIND_REPORT)}
    )
    assert compute_window(unsupported, "wind_avg", "mean").aggregate.value is None
    held = assemble(window(60, 120), inputs(row), {"wind_avg": hold()})
    assert compute_window(held, "wind_avg", "mean").aggregate.value is None


def test_reported_invalid_tie_cannot_leave_valid_member_as_an_implicit_winner() -> None:
    good = sample(
        4,
        60,
        "wind",
        metric="wind_avg",
        kind="reported_interval",
        policy=WIND_REPORT,
        qualified=True,
        reported_seconds=60,
    )
    invalid = sample(
        None,
        60,
        "null_tie",
        metric="wind_avg",
        kind="reported_interval",
        policy=WIND_REPORT,
        reported_seconds=60,
    )
    result = compute_window(
        assemble(window(), inputs(good, invalid), {"wind_avg": report(WIND_REPORT)}),
        "wind_avg",
        "mean",
    )
    assert result.aggregate.value is None and result.aggregate.coverage.fraction == 0


def test_rain_requires_calibrated_support_and_authority_and_conserves_boundary_estimate() -> None:
    row = sample(
        4,
        60,
        "rain",
        metric="precipitation",
        kind="preceding_minute",
        policy=RAIN_REPORT,
        qualified=True,
        reported_seconds=60,
    )
    policy = {"precipitation": report(RAIN_REPORT, "obs_st")}
    halves = [
        compute_window(
            assemble(window(start, end), inputs(row), policy),
            "precipitation",
            "observed_accumulation",
        )
        for start, end in ((0, 30), (30, 60))
    ]
    assert sum(item.exact.value for item in halves) == 4  # type: ignore[misc]
    assert all(
        item.aggregate.reason == "Estimated using uniform intensity over support."
        for item in halves
    )
    unqualified = compute_window(
        assemble(window(), inputs(replace(row, support_qualified=False)), policy),
        "precipitation",
        "observed_accumulation",
    )
    assert unqualified.aggregate.value is None
    assert unqualified.aggregate.reason == "unknown_or_missing_accumulation_support"
    with pytest.raises(ValueError, match="authority"):
        assemble(window(), inputs(row), {"precipitation": report(RAIN_REPORT)})
    with pytest.raises(ValueError, match="Amounts/counts"):
        assemble(window(), inputs(row), {"precipitation": hold()})


def test_overlapping_unique_amounts_require_reconciliation_not_a_silent_sum() -> None:
    first = sample(
        4,
        60,
        "rain_a",
        metric="precipitation",
        kind="reported_interval",
        policy=RAIN_REPORT,
        qualified=True,
        reported_seconds=60,
    )
    other = replace(
        first,
        point=first.point.model_copy(update={"evidence_id": "rain_b"}),
        observation_id=uuid5(NAMESPACE_DNS, "rain_b"),
    )
    assembled = assemble(
        window(), inputs(first, other), {"precipitation": report(RAIN_REPORT, "obs_st")}
    )
    with pytest.raises(ValueError, match="reconciliation"):
        compute_window(assembled, "precipitation", "observed_accumulation")


def test_lightning_one_authority_keeps_integer_subtotal_and_boundary_uncertainty() -> None:
    rows = [
        sample(
            3,
            60,
            "contained",
            metric="lightning_count",
            kind="reported_interval",
            policy=COUNT_REPORT,
            qualified=True,
            reported_seconds=60,
        ),
        sample(
            2,
            120,
            "boundary",
            metric="lightning_count",
            kind="reported_interval",
            policy=COUNT_REPORT,
            qualified=True,
            reported_seconds=60,
        ),
        sample(
            200,
            60,
            "other",
            metric="lightning_count",
            family="obs_air",
            kind="reported_interval",
            policy=COUNT_REPORT,
            qualified=True,
            reported_seconds=60,
        ),
    ]
    assembled = assemble(
        window(0, 90), inputs(*rows), {"lightning_count": report(COUNT_REPORT, "obs_st")}
    )
    result = compute_window(assembled, "lightning_count", "count")
    assert result.aggregate.value == 3 and type(result.aggregate.value) is int
    assert result.aggregate.reason == "boundary_uncertain"
    assert result.details is not None
    assert result.details.boundary_uncertain_count == 2  # type: ignore[union-attr]


def test_wind_cross_metric_support_uses_same_window_and_never_rapid_speed() -> None:
    rows = [
        sample(
            4,
            60,
            "speed",
            metric="wind_avg",
            kind="reported_interval",
            policy=WIND_REPORT,
            qualified=True,
            reported_seconds=60,
        ),
        sample(359, 0, "north_west", metric="wind_direction"),
        sample(1, 30, "north_east", metric="wind_direction"),
    ]
    policies = {"wind_avg": report(WIND_REPORT), "wind_direction": hold(30)}
    result = compute_window(
        assemble(window(), inputs(*rows), policies), "wind_direction", "vector_mean"
    )
    assert result.aggregate.value == 0 and result.aggregate.coverage.fraction == 1
    rapid_only = inputs(sample(4, 0, "rapid", metric="wind_speed", family="rapid_wind"), *rows[1:])
    unavailable = compute_window(
        assemble(window(), rapid_only, {"wind_speed": hold(), "wind_direction": hold(30)}),
        "wind_direction",
        "vector_mean",
    )
    assert unavailable.aggregate.value is None


def test_identity_is_order_independent_and_binds_quality_without_watermark_change() -> None:
    first, second = sample(2, 0, "first"), sample(4, 30, "second")
    policies = {"temperature": hold(30)}
    a = assemble(window(), inputs(first, second), policies)
    b = assemble(window(), inputs(second, first, first), policies)
    assert a.evidence_revision == b.evidence_revision
    corrected = assemble(
        window(), inputs(replace(first, quality_revision="corrected_quality"), second), policies
    )
    assert a.evidence_revision != corrected.evidence_revision
    assert (
        compute_window(a, "temperature", "mean").aggregate.value
        == compute_window(
            corrected,
            "temperature",
            "mean",
        ).aggregate.value
    )
    assert (
        compute_window(a, "temperature", "mean").aggregate_id
        != compute_window(
            corrected,
            "temperature",
            "mean",
        ).aggregate_id
    )
    assert (
        a.evidence_revision
        != assemble(window(), inputs(first, second, watermark="10"), policies).evidence_revision
    )
    assert (
        a.evidence_revision
        != assemble(window(0, 59), inputs(first, second), policies).evidence_revision
    )
    revised = assemble(window(), inputs(first, second), {"temperature": hold(30, "policy_v2")})
    assert a.policy_revision != revised.policy_revision
    assert (
        compute_window(a, "temperature", "mean").aggregate.algorithm_revision
        != compute_window(
            revised,
            "temperature",
            "mean",
        ).aggregate.algorithm_revision
    )
    with pytest.raises(TypeError):
        a.supports["temperature"] = ()  # type: ignore[index]


def test_same_evidence_id_with_different_safe_inputs_is_rejected() -> None:
    row = sample(2, 0, "same")
    with pytest.raises(ValueError, match="Conflicting evidence identity"):
        assemble(
            window(), inputs(row, replace(row, support_policy="changed")), {"temperature": hold()}
        )


def test_unknown_policy_remains_missing_and_all_safe_metadata_still_binds_identity() -> None:
    row = sample(2, 0, "source")
    a = assemble(window(), inputs(row), {})
    b = assemble(window(), inputs(replace(row, support_policy="different")), {})
    assert compute_window(a, "temperature", "mean").aggregate.value is None
    assert a.evidence_revision != b.evidence_revision


def test_reported_bounds_are_explicit_and_not_derived_from_received_at_or_spacing() -> None:
    row = sample(
        4,
        60,
        "wind",
        metric="wind_avg",
        kind="reported_interval",
        policy=WIND_REPORT,
        qualified=True,
        reported_seconds=60,
    )
    with pytest.raises(ValueError, match="source bounds"):
        assemble(window(), inputs(row), {"wind_avg": report(WIND_REPORT, maximum=30)})
    with pytest.raises(ValueError, match="explicit source interval"):
        assemble(
            window(),
            inputs(replace(row, point=row.point.model_copy(update={"support_interval": None}))),
            {"wind_avg": report(WIND_REPORT)},
        )
    assert (
        required_read_spans(
            {"temperature": hold(Fraction(1, 1_000_000)), "wind_avg": report(WIND_REPORT)}
        ).preceding_hold_seconds
        == 1
    )
    assert required_read_spans({"wind_avg": report(WIND_REPORT)}).following_report_seconds == 60
    assert required_read_spans({}).preceding_hold_seconds == 0


@pytest.mark.parametrize("cap", [0, -1, True, float("nan"), Fraction(1, 3)])
def test_invalid_or_submicrosecond_hold_policies_are_rejected(cap: object) -> None:
    with pytest.raises(ValueError):
        SupportPolicy("forward_hold", "synthetic", hold_seconds=cap)  # type: ignore[arg-type]


def test_unqualified_policy_tags_cannot_be_promoted_by_configuration() -> None:
    with pytest.raises(ValueError, match="qualified policy"):
        report("v171-rain-firmware-unqualified-1", "obs_st")


def test_invalid_rain_tie_stays_unavailable_instead_of_counting_the_valid_member() -> None:
    good = sample(
        4,
        60,
        "rain_good",
        metric="precipitation",
        kind="reported_interval",
        policy=RAIN_REPORT,
        qualified=True,
        reported_seconds=60,
    )
    bad = sample(
        None,
        60,
        "rain_invalid",
        metric="precipitation",
        kind="reported_interval",
        policy=RAIN_REPORT,
        reported_seconds=60,
    )
    assembled = assemble(
        window(), inputs(good, bad), {"precipitation": report(RAIN_REPORT, "obs_st")}
    )
    result = compute_window(assembled, "precipitation", "observed_accumulation")
    assert result.aggregate.value is None
    assert result.aggregate.coverage.fraction == 0


def test_exact_projection_and_publication_membership_mismatches_fail_closed() -> None:
    row = sample(2, 0, "source")
    with pytest.raises(ValueError, match="Exact support value"):
        assemble(window(), inputs(replace(row, exact_value=Decimal(3))), {"temperature": hold()})
    with pytest.raises(ValueError, match="public evidence binding"):
        assemble(window(), inputs(replace(row, publication_seq=10)), {"temperature": hold()})
