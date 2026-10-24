# SPDX-License-Identifier: MIT
"""Golden contract vectors and privacy/ownership regressions, using synthetic sites."""

import io
import json
import logging
import zoneinfo
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta, timezone
from fractions import Fraction
from importlib.resources import files
from pathlib import Path

import pytest
import tzdata
from weather.timemodel import (
    FPS,
    IANA_VERSION,
    TZDATA_VERSION,
    FrameAllocation,
    FrameMap,
    HourSlot,
    ScheduleError,
    Segment,
    SiteConfig,
    allocate_frames,
    assign_capture,
    day_for,
    hour_slots,
    load_site,
    site_timezone,
    slot_for,
    solar_cycle,
    weather_day,
)
from weather.timemodel import site as site_module
from weather.timemodel import solar as solar_module
from weather.timemodel._instant import epoch_seconds

ROOT = Path(__file__).resolve().parents[2]
VECTORS = json.loads((ROOT / "docs/contracts/time-model-vectors.json").read_text())
START = datetime(2026, 10, 23, tzinfo=UTC)
SYNTHETIC = SiteConfig(latitude=0, longitude=-90)


def rational(value):
    return Fraction(value["numerator"], value["denominator"])


@pytest.mark.parametrize("case", VECTORS["allocation"], ids=lambda case: case["name"])
def test_allocation_vectors(case):
    allocation = allocate_frames(case["day_seconds"], case["night_seconds"])
    assert allocation.day_frames == case["day_frames"]
    assert allocation.night_frames == case["night_frames"]
    assert allocation.day_output_seconds == rational(case["day_output_seconds"])
    assert allocation.night_output_seconds == rational(case["night_output_seconds"])
    assert allocation.day_terminal_ticks == case["day_terminal_ticks"]
    assert allocation.night_terminal_ticks == case["night_terminal_ticks"]
    assert allocation.day_frames + allocation.night_frames == 7200


@pytest.mark.parametrize("case", VECTORS["frame_centers"]["centers"])
def test_frame_center_vectors(case):
    source = VECTORS["frame_centers"]
    mapping = FrameMap(
        START,
        START + timedelta(seconds=source["source_duration_seconds"]),
        source["frame_count"],
    )
    frame = case["frame"]
    assert mapping.source_offset(frame) == rational(case["source_offset_seconds"])
    assert mapping.source_center(frame) == epoch_seconds(START) + mapping.source_offset(frame)
    assert mapping.output_pts(frame) == rational(case["output_pts_seconds"])
    assert mapping.output_duration == 70
    assert mapping.terminal_ticks == 4200000


@pytest.mark.parametrize(
    "case",
    VECTORS["civil_calendar_days"],
    ids=lambda case: case["local_date"],
)
def test_every_calendar_vector(case):
    local_date = date.fromisoformat(case["local_date"])
    day = weather_day(local_date)
    slots = hour_slots(local_date)
    assert len(slots) == case["slot_count"]
    assert slots[0].start == day.start
    assert slots[-1].end == day.end
    for slot, expected in zip(slots, case["slots"], strict=True):
        assert slot.start.isoformat().replace("+00:00", "Z") == expected["start"]
        assert slot.end.isoformat().replace("+00:00", "Z") == expected["end"]
        assert slot.local_label == expected["local_label"]
        assert slot.fold == expected["fold"]
        assert slot_for(slot.start + timedelta(minutes=59)) == slot
        assert day_for(slot.start) == day


def test_packaged_rules_bypass_system_search_without_global_mutation(monkeypatch):
    system_path = zoneinfo.TZPATH
    expected = files("tzdata.zoneinfo").joinpath("America", "Denver").read_bytes()
    seen = []

    class PackagedOnly:
        def __new__(cls, *args, **kwargs):
            pytest.fail("The time model must not search system TZPATH")

        @staticmethod
        def from_file(source, *, key):
            actual = source.read()
            assert actual == expected
            seen.append(key)
            return zoneinfo.ZoneInfo.from_file(io.BytesIO(actual), key=key)

    site_timezone.cache_clear()
    monkeypatch.setattr(site_module, "ZoneInfo", PackagedOnly)
    try:
        assert site_timezone().key == "America/Denver"
        assert site_timezone() is site_timezone()
    finally:
        site_timezone.cache_clear()
    assert seen == ["America/Denver"]
    assert zoneinfo.TZPATH == system_path
    assert TZDATA_VERSION == tzdata.__version__ == "2026.5"
    assert IANA_VERSION == tzdata.IANA_VERSION


def test_precise_submicrosecond_centers_and_utc_normalization():
    local = START.astimezone(timezone(timedelta(hours=2)))
    mapping = FrameMap(local, local + timedelta(microseconds=7), 3)
    assert mapping.start == START
    assert mapping.start.tzinfo is UTC
    assert mapping.source_step == Fraction(7, 3000000)
    assert mapping.source_center(0) == epoch_seconds(START) + Fraction(7, 6000000)
    assert mapping.source_center(2) < epoch_seconds(mapping.end)
    assert mapping.output_duration == Fraction(1, 20)
    assert mapping.terminal_ticks == 3000
    assert FPS == Fraction(60, 1)


def test_boundaries_are_shared_once_and_next_dawn_uses_a_local_date(monkeypatch):
    calls = []

    def dawn(observer, local_date, *, depression, tzinfo):
        calls.append((local_date, observer.elevation, depression, tzinfo.key))
        return datetime.combine(local_date, time(12), UTC) + timedelta(seconds=local_date.day)

    def dusk(observer, local_date, *, depression, tzinfo):
        assert observer.elevation == 0 and depression == 6
        return datetime.combine(local_date, time(22), UTC)

    monkeypatch.setattr(solar_module.astral_sun, "dawn", dawn)
    monkeypatch.setattr(solar_module.astral_sun, "dusk", dusk)
    monkeypatch.setattr(
        solar_module.astral_sun,
        "sunrise",
        lambda observer, local_date, **kwargs: datetime.combine(local_date, time(13), UTC),
    )
    monkeypatch.setattr(
        solar_module.astral_sun,
        "sunset",
        lambda observer, local_date, **kwargs: datetime.combine(local_date, time(21), UTC),
    )
    configured = replace(
        SYNTHETIC,
        elevation_meters=5000,
        dawn_offset_seconds=60,
        dusk_offset_seconds=-30,
    )
    cycle = solar_cycle(date(2026, 3, 7), configured)
    following = solar_cycle(date(2026, 3, 8), configured)
    assert [call[0] for call in calls] == [
        date(2026, 3, 7),
        date(2026, 3, 8),
        date(2026, 3, 8),
        date(2026, 3, 9),
    ]
    assert all(call[1:] == (0, 6, "America/Denver") for call in calls)
    assert cycle.dawn == datetime(2026, 3, 7, 12, 1, 7, tzinfo=UTC)
    assert cycle.dusk == datetime(2026, 3, 7, 21, 59, 30, tzinfo=UTC)
    assert cycle.daytime.end == cycle.overnight.start == cycle.dusk
    assert cycle.next_dawn == following.dawn
    assert cycle.next_dawn - cycle.dawn != timedelta(days=1)
    assert cycle.sunrise == datetime(2026, 3, 7, 13, tzinfo=UTC)
    assert cycle.sunset == datetime(2026, 3, 7, 21, tzinfo=UTC)


def test_synthetic_year_has_real_elapsed_solar_lengths():
    durations = set()
    previous = None
    for index in range(365):
        cycle = solar_cycle(date(2026, 1, 1) + timedelta(days=index), SYNTHETIC)
        assert cycle.dawn < cycle.dusk < cycle.next_dawn
        assert cycle.allocation.day_frames + cycle.allocation.night_frames == 7200
        assert cycle.cycle_date == cycle.dawn.astimezone(site_timezone()).date()
        assert (
            cycle.daytime.frame_map.output_duration + cycle.overnight.frame_map.output_duration
            == 120
        )
        assert cycle.config_hash == SYNTHETIC.config_hash
        if previous is not None:
            assert previous.next_dawn == cycle.dawn
        durations.add(cycle.next_dawn - cycle.dawn)
        previous = cycle
    assert len(durations) > 1
    assert durations != {timedelta(days=1)}


def test_polar_missing_event_and_bad_shift_are_visible_errors():
    with pytest.raises(ScheduleError):
        solar_cycle(date(2026, 6, 21), SiteConfig(latitude=89, longitude=-90))
    with pytest.raises(ScheduleError):
        solar_cycle(date(2026, 10, 23), replace(SYNTHETIC, dusk_offset_seconds=-86400))
    with pytest.raises(ScheduleError):
        solar_cycle(date.max, SYNTHETIC)
    with pytest.raises(ScheduleError, match="horizon"):
        solar_cycle(date(2026, 10, 23), replace(SYNTHETIC, horizon_model="unimplemented/v2"))


def test_site_altitude_is_not_an_observer_horizon_correction():
    original = solar_cycle(date(2026, 10, 23), SYNTHETIC)
    elevated = solar_cycle(date(2026, 10, 23), replace(SYNTHETIC, elevation_meters=1000000))
    assert (original.dawn, original.dusk, original.next_dawn) == (
        elevated.dawn,
        elevated.dusk,
        elevated.next_dawn,
    )
    assert original.config_hash != elevated.config_hash
    assert SYNTHETIC.config_hash != replace(SYNTHETIC, horizon_model="next-model/v2").config_hash


def test_shared_half_open_assignment_and_strict_uncertainty():
    cycle = solar_cycle(date(2026, 10, 23), SYNTHETIC)
    following = solar_cycle(date(2026, 10, 24), SYNTHETIC)
    segments = (*cycle.segments, *following.segments)
    assert assign_capture(cycle.dawn, segments).segment == cycle.daytime
    assert assign_capture(cycle.dusk, segments).segment == cycle.overnight
    assert assign_capture(cycle.next_dawn, segments).segment == following.daytime
    assert (
        assign_capture(cycle.dawn - timedelta(microseconds=1), segments).status
        == "outside_schedule"
    )
    bounds = {
        "lower_bound": cycle.dusk - timedelta(microseconds=1),
        "upper_bound": cycle.dusk,
    }
    strict = assign_capture(cycle.dusk, segments, strict=True, **bounds)
    assert strict.status == "boundary_ambiguous" and strict.segment is None
    assert assign_capture(cycle.dusk, segments, **bounds).segment == cycle.overnight
    assert assign_capture(cycle.dusk, segments, strict=True).segment == cycle.overnight
    assert (
        assign_capture(
            cycle.dusk + timedelta(seconds=1),
            segments,
            lower_bound=cycle.dusk,
            upper_bound=cycle.dusk + timedelta(seconds=2),
            strict=True,
        ).segment
        == cycle.overnight
    )


def test_assignment_refuses_overlaps_or_invalid_uncertainty():
    cycle = solar_cycle(date(2026, 10, 23), SYNTHETIC)
    with pytest.raises(ScheduleError, match="overlap"):
        assign_capture(cycle.dawn, [cycle.daytime, cycle.daytime])
    with pytest.raises(ScheduleError, match="Both"):
        assign_capture(cycle.dawn, cycle.segments, lower_bound=cycle.dawn)
    with pytest.raises(ScheduleError, match="contain"):
        assign_capture(
            cycle.dawn,
            cycle.segments,
            lower_bound=cycle.dawn + timedelta(seconds=1),
            upper_bound=cycle.dawn + timedelta(seconds=2),
        )


def test_segment_labels_and_offset_labels():
    cycle = solar_cycle(date(2026, 10, 23), SYNTHETIC)
    assert cycle.daytime.label == "Daytime · Oct 23"
    assert cycle.overnight.label == "Overnight · Oct 23–24"
    assert cycle.daytime.local_start.endswith("-06:00")
    assert cycle.overnight.local_end.endswith("-06:00")
    assert solar_cycle(date(2026, 10, 31), SYNTHETIC).overnight.label == "Overnight · Oct 31–Nov 1"
    assert solar_cycle(date(2026, 12, 31), SYNTHETIC).overnight.label == "Overnight · Dec 31–Jan 1"


def test_public_value_types_refuse_invalid_states():
    cycle = solar_cycle(date(2026, 10, 23), SYNTHETIC)
    for allocation in [(0, 7200), (7200, 0), (1, 1), (1.0, 7199)]:
        with pytest.raises(ScheduleError):
            FrameAllocation(*allocation)
    with pytest.raises(ScheduleError):
        replace(cycle, dusk=cycle.dawn)
    with pytest.raises(ScheduleError):
        replace(cycle, cycle_date=cycle.cycle_date + timedelta(days=1))
    with pytest.raises(ScheduleError):
        replace(cycle, allocation=FrameAllocation(1, 7199))
    with pytest.raises(ScheduleError):
        Segment("invalid", date(2026, 10, 23), START, START + timedelta(hours=1), 1)
    with pytest.raises(ScheduleError):
        weather_day(datetime(2026, 10, 23, tzinfo=UTC))
    with pytest.raises(ScheduleError):
        weather_day(date.max)
    with pytest.raises(ScheduleError):
        HourSlot(START + timedelta(seconds=1), day_for(START))


def _reject_duration():
    for day, night in [(0, 1), (1, 0), (-1, 1), (1, -1), (1, 15999), (15999, 1)]:
        with pytest.raises(ScheduleError):
            allocate_frames(day, night)


def _reject_cadence():
    with pytest.raises(ScheduleError):
        allocate_frames(1, 1, fps=Fraction(60000, 1001))
    with pytest.raises(ScheduleError):
        FrameMap(START, START + timedelta(hours=1), 1, Fraction(60000, 1001))


def _next_dawn_is_not_fixed():
    cycle = solar_cycle(date(2026, 10, 23), SYNTHETIC)
    assert cycle.next_dawn == solar_cycle(date(2026, 10, 24), SYNTHETIC).dawn
    assert cycle.next_dawn != cycle.dawn + timedelta(seconds=86400)


def _repeated_hour_identity():
    slots = hour_slots(date(2026, 11, 1))
    repeated = [slot for slot in slots if slot.local_label[11:16] == "01:00"]
    assert len(repeated) == 2 and repeated[0] != repeated[1]
    assert [slot.fold for slot in repeated] == [0, 1]


def _reject_cross_day_slot():
    with pytest.raises(ScheduleError):
        HourSlot(weather_day(date(2026, 10, 24)).start, weather_day(date(2026, 10, 23)))


def _reject_intervals():
    for end in [START, START - timedelta(microseconds=1)]:
        with pytest.raises(ScheduleError):
            FrameMap(START, end, 1)


def _reject_decimal_authority():
    mapping = FrameMap(START, START + timedelta(seconds=1), 3)
    with pytest.raises(ScheduleError):
        mapping.source_center(1.0)
    with pytest.raises(ScheduleError):
        mapping.output_pts(1.0)
    with pytest.raises(ScheduleError):
        allocate_frames(1.0, 1)


def _reject_neighbor_context():
    mapping = FrameMap(START, START + timedelta(seconds=1), 3)
    assert epoch_seconds(mapping.start) < mapping.source_center(0)
    assert mapping.source_center(2) < epoch_seconds(mapping.end)
    for index in [-1, 3]:
        with pytest.raises(ScheduleError):
            mapping.source_center(index)


NEGATIVE_CHECKS = {
    (
        "Reject zero/negative segment durations and zero-frame allocations; "
        "day=1 night=15999 rounds daytime to zero and must fail"
    ): _reject_duration,
    "Reject 60000/1001 fps; 7200 frames then take 120.12 seconds": _reject_cadence,
    "Never derive next dawn by adding 86400 seconds": _next_dawn_is_not_fixed,
    "Never collapse the two repeated 01:00 slots": _repeated_hour_identity,
    "Reject cross-day still/slot FK even if local labels match": _reject_cross_day_slot,
    "Reject reversed/empty source intervals even if schema shape passes": _reject_intervals,
    "Never use JSON decimal seeks as authoritative frame ownership": _reject_decimal_authority,
    "No neighboring-segment temporal context": _reject_neighbor_context,
}


@pytest.mark.parametrize("obligation", VECTORS["negative_obligations"])
def test_negative_golden_obligation(obligation):
    NEGATIVE_CHECKS[obligation]()


def test_private_config_loaders_hash_and_redacted_logging(tmp_path, caplog, monkeypatch):
    synthetic = {"latitude": "12.3456789", "longitude": "-87.6543219"}
    path = tmp_path / "site.json"
    path.write_text(json.dumps(synthetic))
    config = load_site(path, environ={"WEATHER_SITE_LATITUDE": "invalid"})
    from_env = load_site(
        environ={
            "WEATHER_SITE_LATITUDE": synthetic["latitude"],
            "WEATHER_SITE_LONGITUDE": synthetic["longitude"],
            "WEATHER_SITE_DAWN_OFFSET_SECONDS": "0",
        }
    )
    assert config == from_env
    assert config.config_hash == from_env.config_hash
    assert len(config.config_hash) == 64
    assert load_site(environ={"WEATHER_SITE_FILE": str(path)}) == config
    monkeypatch.setenv("WEATHER_SITE_FILE", str(path))
    assert load_site() == config
    caplog.set_level(logging.INFO)
    logging.getLogger("weather.timemodel.site").info("Private site is %r", config)
    assert repr(config) == "SiteConfig(<private>)"
    for value in synthetic.values():
        assert value not in repr(config) and value not in caplog.text
    path.write_text(json.dumps(synthetic) + "not valid JSON")
    try:
        load_site(path, environ=synthetic)
    except ScheduleError:
        logging.getLogger("weather.timemodel.site").exception("Invalid private site")
    for value in synthetic.values():
        assert value not in caplog.text


@pytest.mark.parametrize(
    "value",
    [None, True, [], "not-a-number", "NaN", float("inf"), 91, 10**1000],
)
def test_invalid_private_coordinates_are_sanitized(value):
    with pytest.raises(ScheduleError):
        SiteConfig.from_mapping({"latitude": value, "longitude": 0})


@pytest.mark.parametrize("value", [True, 0.5, "not-an-offset"])
def test_invalid_private_offsets(value):
    with pytest.raises(ScheduleError):
        SiteConfig.from_mapping({"latitude": 0, "longitude": 0, "dawn_offset_seconds": value})


@pytest.mark.parametrize(
    "extra",
    [{"unexpected": "private"}, {"timezone": "UTC"}, {"timezone": 3}, {"horizon_model": []}],
)
def test_invalid_private_fields(extra):
    with pytest.raises(ScheduleError):
        SiteConfig.from_mapping({"latitude": 0, "longitude": 0, **extra})


def test_invalid_private_file_has_no_environment_fallback(tmp_path):
    env = {"WEATHER_SITE_LATITUDE": "0", "WEATHER_SITE_LONGITUDE": "-90"}
    path = tmp_path / "missing.json"
    with pytest.raises(ScheduleError):
        load_site(path, environ=env)
    for content in ["[]", '{"latitude":0,"latitude":1,"longitude":0}', "{}"]:
        path.write_text(content)
        with pytest.raises(ScheduleError):
            load_site(path, environ=env)
    with pytest.raises(ScheduleError):
        load_site(environ={})


def test_remaining_invalid_geometry_inputs():
    with pytest.raises(ScheduleError):
        FrameMap(datetime(2026, 10, 23), START + timedelta(hours=1), 1)
    with pytest.raises(ScheduleError):
        day_for(datetime(2026, 10, 23))
    with pytest.raises(ScheduleError):
        day_for(datetime.min.replace(tzinfo=UTC))
    for count in [0, 7201, True, 1.0]:
        with pytest.raises(ScheduleError):
            FrameMap(START, START + timedelta(hours=1), count)
    with pytest.raises(ScheduleError):
        allocate_frames(True, 1)
    with pytest.raises(ScheduleError):
        allocate_frames(1, 1, fps=60.0)
    with pytest.raises(ScheduleError):
        SiteConfig(0, 0, horizon_model="")
