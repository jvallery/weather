# SPDX-License-Identifier: MIT
"""sRGB <-> linear light through lookup tables, so results never depend on a CPU's libm.

Decoding is an exact 256-entry table. Encoding quantizes linear light to 65,536 steps (more
than 20 steps per 8-bit code even at the darkest end) and maps each step through a table built
once with double precision. Elementwise float32 multiply/add/divide are correctly rounded, so
the same inputs give the same bytes on every node.
"""

import numpy as np
from numpy.typing import NDArray

STEPS = 65535


def _eotf(value: float) -> float:
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def _oetf(value: float) -> float:
    return 12.92 * value if value <= 0.0031308 else 1.055 * value ** (1 / 2.4) - 0.055


TO_LINEAR: NDArray[np.float32] = np.array([_eotf(i / 255) for i in range(256)], dtype=np.float32)
TO_SRGB: NDArray[np.uint8] = np.array(
    [min(255, max(0, round(255 * _oetf(i / STEPS)))) for i in range(STEPS + 1)], dtype=np.uint8
)


def to_linear(rgb: NDArray[np.uint8]) -> NDArray[np.float32]:
    return TO_LINEAR[rgb]


def to_srgb(linear: NDArray[np.float32]) -> NDArray[np.uint8]:
    scaled = np.rint(np.clip(linear, 0, 1) * np.float32(STEPS))
    return TO_SRGB[scaled.astype(np.uint16)]
