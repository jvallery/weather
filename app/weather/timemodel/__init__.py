# SPDX-License-Identifier: MIT
"""Shared, deterministic civil-solar, frame, calendar and capture time model."""

from .allocation import (
    FPS,
    TIMESCALE,
    TOTAL_FRAMES,
    FrameAllocation,
    FrameMap,
    allocate_frames,
)
from .assign import Assignment, assign_capture
from .calendar import HourSlot, WeatherDay, day_for, hour_slots, slot_for, weather_day
from .errors import ScheduleError
from .site import IANA_VERSION, TZDATA_VERSION, SiteConfig, load_site, site_timezone
from .solar import Segment, SolarCycle, solar_cycle

__all__ = [
    "FPS",
    "IANA_VERSION",
    "TIMESCALE",
    "TOTAL_FRAMES",
    "TZDATA_VERSION",
    "Assignment",
    "FrameAllocation",
    "FrameMap",
    "HourSlot",
    "ScheduleError",
    "Segment",
    "SiteConfig",
    "SolarCycle",
    "WeatherDay",
    "allocate_frames",
    "assign_capture",
    "day_for",
    "hour_slots",
    "load_site",
    "site_timezone",
    "slot_for",
    "solar_cycle",
    "weather_day",
]
