# SPDX-License-Identifier: MIT
"""Safe schedule failures: callers expose the reason, never private configuration."""


class ScheduleError(ValueError):
    """The requested time geometry or private site configuration is invalid."""
