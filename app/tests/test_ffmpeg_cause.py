# SPDX-License-Identifier: MIT
"""A mezzanine decode failure is reduced to a fixed cause token, never FFmpeg's text."""

from weather.logs import safe_fields
from weather.pipeline.ffmpeg import _STDERR_CAUSES, FFmpegError, stderr_cause


def test_stderr_is_reduced_to_fixed_tokens() -> None:
    assert stderr_cause(b"") == "empty"
    assert stderr_cause(b"  \n") == "empty"
    assert stderr_cause(b"/staging/x/blocks/00012.mkv: Input/output error\n") == "io_error"
    assert stderr_cause(b"[matroska] Invalid data found when processing input") == "invalid_data"
    assert stderr_cause(b"Impossible to open '/staging/a b.mkv'") == "concat_open"
    assert stderr_cause(b"something new from a later FFmpeg") == "other"


def test_tokens_and_error_fields_are_loggable() -> None:
    tokens = {token for _, token in _STDERR_CAUSES}
    tokens |= {"empty", "other", "short_frame", "exit_status"}
    for token in tokens:
        assert safe_fields({"cause": token}) == {"cause": token}
    error = FFmpegError("mezzanine_decode", "short_frame", 1234)
    assert (error.stage, error.cause, error.frame) == ("mezzanine_decode", "short_frame", 1234)
    assert str(error) == "FFmpeg mezzanine_decode failed"
    legacy = FFmpegError("renditions")
    assert legacy.cause is None and legacy.frame is None
