# SPDX-License-Identifier: MIT
"""#147: structured JSON log lines, safe by construction."""

import json
import logging

import pytest
from weather.logs import JsonFormatter, event, safe_fields


def render(record: logging.LogRecord) -> dict[str, object]:
    return json.loads(JsonFormatter("collector").format(record))


def capture(name: str, level: int = logging.INFO, **fields: object) -> dict[str, object]:
    records: list[logging.LogRecord] = []
    logger = logging.getLogger("weather.test")
    handler = logging.Handler()
    handler.emit = records.append  # type: ignore[method-assign]
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        event(logger, name, level, **fields)
    finally:
        logger.removeHandler(handler)
    return render(records[0])


def test_an_event_is_one_json_line_with_stable_keys() -> None:
    line = capture("staging_stop", logging.WARNING, camera="skycam_east", ratio=0.9012)
    assert line["event"] == "staging_stop" and line["msg"] == "staging_stop"
    assert (line["camera"], line["ratio"], line["level"], line["component"]) == (
        "skycam_east",
        0.9012,
        "warning",
        "collector",
    )
    assert str(line["ts"]).endswith("+00:00")


def test_plain_messages_still_render() -> None:
    record = logging.LogRecord("weather.x", logging.ERROR, __file__, 1, "Failed (%s)", ("X",), None)
    assert render(record)["msg"] == "Failed (X)"


@pytest.mark.parametrize(
    "value",
    ["/staging/sealed/skycam_east", "https://camera.example", "user@host", "C:\\x", "a/b"],
)
def test_paths_urls_and_addresses_are_refused(value: str) -> None:
    with pytest.raises(ValueError):
        safe_fields({"reason": value})


def test_only_scalars_are_accepted() -> None:
    with pytest.raises(TypeError):
        safe_fields({"rows": [1, 2]})
    assert safe_fields({"n": 3, "ok": True, "reason": None}) == {
        "n": 3,
        "ok": True,
        "reason": None,
    }


def test_tracebacks_are_reduced_to_the_error_type() -> None:
    try:
        raise RuntimeError("postgresql://secret@host/db")
    except RuntimeError:
        import sys

        record = logging.LogRecord("w", logging.ERROR, __file__, 1, "Failed", (), sys.exc_info())
    line = render(record)
    assert line["error"] == "RuntimeError"
    assert "secret" not in json.dumps(line)
