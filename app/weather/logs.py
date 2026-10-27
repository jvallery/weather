# SPDX-License-Identifier: MIT
"""Structured JSON log lines (#147): one line per state change, diagnosable from Loki alone.

Every process calls `configure(component)` once. Existing `LOG.info("text %s", value)` calls
keep working and become `{"msg": ...}` lines. `event(logger, name, **fields)` adds stable keys
(`event`, `camera`, `segment_id`, `reason`, counts). Field values must be scalars, and a value
that looks like a path or URL is refused, so no staging path, object key, camera address or
SQL URL can reach the logs through this helper. Tracebacks are never logged, only the error
type.
"""

import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any

_UNSAFE = re.compile(r"/|\\|://|@")
_SCALARS = (str, int, float, bool, type(None))
QUIET = ("httpx", "httpcore", "httpx2", "httpcore2", "botocore", "boto3", "urllib3", "aiobotocore")


class JsonFormatter(logging.Formatter):
    def __init__(self, component: str) -> None:
        super().__init__()
        self.component = component

    def format(self, record: logging.LogRecord) -> str:
        document: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "component": self.component,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            document.update(fields)
        if record.exc_info and record.exc_info[0] is not None:
            document["error"] = record.exc_info[0].__name__
        return json.dumps(document, sort_keys=True, default=str)


def configure(component: str, level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter(component))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # HTTP clients log every request with its full URL (bucket and object keys) at INFO.
    for name in QUIET:
        logging.getLogger(name).setLevel(logging.WARNING)


def safe_fields(fields: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in fields.items():
        if not isinstance(value, _SCALARS):
            raise TypeError(f"Log field {key} must be a scalar")
        if isinstance(value, str) and _UNSAFE.search(value):
            raise ValueError(f"Log field {key} looks like a path or URL")
        clean[key] = value
    return clean


def event(logger: logging.Logger, name: str, level: int = logging.INFO, **fields: Any) -> None:
    """One structured state-change line: `{"event": name, ...fields}`. Logging never fails
    its caller: a value `safe_fields` refuses is replaced by `"refused"`."""
    clean: dict[str, Any] = {}
    for key, value in fields.items():
        try:
            clean.update(safe_fields({key: value}))
        except TypeError, ValueError:
            clean[key] = "refused"
    logger.log(level, name, extra={"fields": {"event": name, **clean}})
