# SPDX-License-Identifier: MIT
"""Sanitized preview derivatives: decoded, resized and re-encoded, never copied.

The camera's original bytes (and any EXIF, XMP, GPS, ICC or comment they carry) never reach a
derivative: the pixels are rebuilt into a fresh image with no metadata, encoded as a baseline
JPEG, and the result is checked marker by marker. Only SOI, a JFIF APP0, DQT, SOF0, DHT, DRI,
one SOS and EOI may appear; anything else fails the derivative. Full view, no masks (Jason's
decision for #39).
"""

import io

from PIL import Image

# (variant, maximum width); never upscaled.
VARIANTS = (("large", 1280), ("small", 480))
QUALITY = 85
# Baseline segments a metadata-free JFIF may contain before its single scan.
SEGMENTS = {0xDB, 0xC0, 0xC4, 0xDD}


class DerivativeError(ValueError):
    """The source could not be decoded, or an encoded derivative carries metadata."""


def check_clean(data: bytes) -> None:
    """Raise unless `data` is a single-scan baseline JFIF with no metadata segment."""
    if data[:2] != b"\xff\xd8" or data[-2:] != b"\xff\xd9":
        raise DerivativeError("Not a complete JPEG")
    at = 2
    while True:
        if at + 4 > len(data) or data[at] != 0xFF:
            raise DerivativeError("Malformed JPEG segment")
        marker = data[at + 1]
        length = int.from_bytes(data[at + 2 : at + 4], "big")
        if length < 2 or at + 2 + length > len(data):
            raise DerivativeError("Malformed JPEG segment")
        body = data[at + 4 : at + 2 + length]
        if marker == 0xE0:
            if not body.startswith(b"JFIF\x00"):
                raise DerivativeError("Unexpected APP0")
        elif marker == 0xDA:
            at += 2 + length
            break
        elif marker not in SEGMENTS:
            raise DerivativeError("Metadata or unsupported JPEG segment")
        at += 2 + length
    # Entropy-coded data: only stuffed 0xFF00 and restart markers before the final EOI.
    end = len(data) - 2
    while (at := data.find(b"\xff", at, end)) != -1:
        following = data[at + 1]
        if following != 0x00 and not 0xD0 <= following <= 0xD7:
            raise DerivativeError("Unexpected marker after the scan")
        at += 2


def derive(source: bytes) -> list[tuple[str, bytes, int, int]]:
    """(variant, JPEG bytes, width, height) for each preview size."""
    try:
        with Image.open(io.BytesIO(source)) as image:
            if image.format != "JPEG":
                raise DerivativeError("Source is not a JPEG")
            width, height = image.size
            largest = min(VARIANTS[0][1], width)
            # Let libjpeg scale while decoding; the result is never smaller than requested.
            image.draft("RGB", (largest, max(1, round(height * largest / width))))
            rgb = image.convert("RGB")
    except (OSError, ValueError, Image.DecompressionBombError) as error:
        if isinstance(error, DerivativeError):
            raise
        raise DerivativeError("Source frame could not be decoded") from None
    results = []
    for variant, maximum in VARIANTS:
        target = min(maximum, rgb.width)
        size = (target, max(1, round(rgb.height * target / rgb.width)))
        resized = rgb if size == rgb.size else rgb.resize(size, Image.Resampling.LANCZOS)
        # A fresh image from raw pixels carries no info dictionary: no EXIF, XMP, ICC or comment.
        clean = Image.frombytes("RGB", resized.size, resized.tobytes())
        buffer = io.BytesIO()
        clean.save(buffer, format="JPEG", quality=QUALITY, optimize=True, progressive=False)
        data = buffer.getvalue()
        check_clean(data)
        results.append((variant, data, clean.width, clean.height))
    return results
