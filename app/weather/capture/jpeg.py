# SPDX-License-Identifier: MIT
"""Bounded SOI/EOI and SOF geometry checks; the renderer decodes and rejects per slot."""

import struct

from .client import CaptureFailure

SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def _next_marker(body: bytes, offset: int) -> int:
    """Offset of the next 0xFF that starts a marker, skipping stuffed 0xFF00 like libjpeg."""
    while True:
        offset = body.find(b"\xff", offset)
        if offset < 0 or offset + 1 >= len(body):
            return -1
        if body[offset + 1] not in (0x00, 0xFF):
            return offset
        offset += 1


def marker_geometry(body: bytes) -> tuple[int, int]:
    if not body.startswith(b"\xff\xd8") or not body.endswith(b"\xff\xd9"):
        raise CaptureFailure("jpeg_markers", 200)
    offset = 2
    while offset < len(body) - 2:
        if body[offset] != 0xFF:
            # The commissioned cameras write a COM segment whose length undercounts its payload,
            # leaving extraneous bytes before the next marker. libjpeg and FFmpeg skip them.
            offset = _next_marker(body, offset)
            if offset < 0:
                break
            continue
        while offset < len(body) and body[offset] == 0xFF:
            offset += 1
        if offset >= len(body):
            break
        marker = body[offset]
        offset += 1
        if marker in {0xD8, 0xD9, 0xDA, 0x00}:
            break
        if 0xD0 <= marker <= 0xD7 or marker == 0x01:
            continue
        if offset + 2 > len(body):
            break
        length = int.from_bytes(body[offset : offset + 2])
        if length < 2 or offset + length > len(body):
            break
        if marker in SOF:
            if length < 8:
                break
            height, width = struct.unpack(">HH", body[offset + 3 : offset + 7])
            return width, height
        offset += length
    raise CaptureFailure("jpeg_markers", 200)


def validate_jpeg(body: bytes, allowed: tuple[int, int], max_bytes: int) -> tuple[int, int]:
    if len(body) > max_bytes:
        raise CaptureFailure("oversized", 200)
    geometry = marker_geometry(body)
    if geometry != allowed:
        raise CaptureFailure("geometry", 200)
    return geometry
