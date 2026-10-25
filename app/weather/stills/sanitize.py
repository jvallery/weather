# SPDX-License-Identifier: MIT
"""Sanitized public comparison stills: full view, no masks, no metadata.

A derivative is re-encoded from the decoded pixels of the archived original (the pinned
FFmpeg's bit-exact decode), never copied or edited from the camera file, so its EXIF, XMP,
IPTC, ICC, comment and GPS segments cannot survive. JPEG and WebP at 1920, 960 and 480 px wide
(never upscaled) use #15's poster encoder settings, so a rerun produces the same bytes. Every
output is scanned before upload: a JPEG may carry only JFIF APP0, tables, frame and scan
markers; a WebP only image chunks, with no metadata flags.
"""

import io
import struct
from dataclasses import dataclass

import numpy as np
from PIL import Image

RECIPE = "sanitized-still/v1"
# The full view for all three cameras (Jason, 2026-10-23); versioned so a later mask is visible.
MASK_POLICY = "none/v1"
# (width, JPEG role, WebP role)
SIZES = (
    (1920, "still", "still_webp"),
    (960, "still_960", "still_webp960"),
    (480, "still_480", "still_webp480"),
)
# SOFn, DHT, DAC, DQT, DRI and DNL: tables and frame/scan structure only.
JPEG_STRUCTURE = frozenset({*range(0xC0, 0xC8), *range(0xC9, 0xD0), 0xDB, 0xDC, 0xDD})
WEBP_CHUNKS = {b"VP8 ", b"VP8L", b"VP8X", b"ALPH"}
# VP8X flags: ICC profile, EXIF and XMP.
WEBP_METADATA_FLAGS = 0x20 | 0x08 | 0x04


class MetadataFound(ValueError):
    """A derivative would carry metadata; it is never uploaded."""


@dataclass(frozen=True)
class Derivative:
    role: str
    extension: str
    data: bytes
    width: int
    height: int


def encode(image: Image.Image, extension: str) -> bytes:
    buffer = io.BytesIO()
    if extension == "jpg":
        image.save(buffer, format="JPEG", quality=88, optimize=False, progressive=False)
    else:
        image.save(buffer, format="WEBP", quality=82, method=4)
    return buffer.getvalue()


def derive(rgb: bytes, width: int, height: int) -> list[Derivative]:
    """The six public images of one original, from its RGB24 pixels; each is scanned."""
    if len(rgb) != width * height * 3:
        raise ValueError("Pixel buffer does not match the geometry")
    image = Image.fromarray(np.frombuffer(rgb, dtype=np.uint8).reshape(height, width, 3), "RGB")
    result: list[Derivative] = []
    for target, jpeg_role, webp_role in SIZES:
        w = min(target, width)
        h = max(1, round(height * w / width))
        scaled = image if w == width else image.resize((w, h), Image.Resampling.LANCZOS)
        for extension, role in (("jpg", jpeg_role), ("webp", webp_role)):
            data = encode(scaled, extension)
            problems = metadata(data)
            if problems:
                raise MetadataFound(",".join(problems))
            result.append(Derivative(role, extension, data, w, h))
    return result


def metadata(data: bytes) -> list[str]:
    """Metadata or unknown structure in a JPEG or WebP; empty when the file is clean."""
    if data[:2] == b"\xff\xd8":
        return _jpeg(data)
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return _webp(data)
    return ["format"]


def _jpeg(data: bytes) -> list[str]:
    found: list[str] = []
    at = 2
    while at < len(data):
        if data[at] != 0xFF:
            return [*found, "structure"]
        marker = data[at + 1] if at + 1 < len(data) else -1
        if marker == 0xFF:  # fill byte
            at += 1
            continue
        if marker == 0xD9:  # EOI
            return found if at + 2 == len(data) else [*found, "trailing"]
        if at + 4 > len(data):
            return [*found, "structure"]
        length = struct.unpack(">H", data[at + 2 : at + 4])[0]
        segment = data[at + 4 : at + 2 + length]
        if marker == 0xE0:
            if not segment.startswith(b"JFIF\x00"):
                found.append("app0")
        elif marker == 0xE1:
            found.append("xmp" if b"http://ns.adobe.com/xap/" in segment[:64] else "exif")
        elif marker == 0xED:
            found.append("iptc")
        elif marker == 0xE2:
            found.append("icc")
        elif 0xE3 <= marker <= 0xEF:
            found.append("app")
        elif marker == 0xFE:
            found.append("comment")
        elif marker == 0xDA:  # SOS: skip the entropy-coded data to the next real marker
            at += 2 + length
            while at + 1 < len(data) and not (
                data[at] == 0xFF and data[at + 1] not in (0x00, *range(0xD0, 0xD8))
            ):
                at += 1
            continue
        elif marker not in JPEG_STRUCTURE:
            found.append("marker")
        at += 2 + length
    return [*found, "structure"]


def _webp(data: bytes) -> list[str]:
    found: list[str] = []
    if struct.unpack("<I", data[4:8])[0] + 8 != len(data):
        return ["structure"]
    at = 12
    while at < len(data):
        if at + 8 > len(data):
            return [*found, "structure"]
        fourcc = data[at : at + 4]
        size = struct.unpack("<I", data[at + 4 : at + 8])[0]
        if fourcc not in WEBP_CHUNKS:
            found.append({b"EXIF": "exif", b"XMP ": "xmp", b"ICCP": "icc"}.get(fourcc, "chunk"))
        elif fourcc == b"VP8X" and data[at + 8] & WEBP_METADATA_FLAGS:
            found.append("flags")
        at += 8 + size + (size & 1)
    return found
