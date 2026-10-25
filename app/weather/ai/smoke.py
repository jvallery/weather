# SPDX-License-Identifier: MIT
"""One bounded live image call through the scoped key; publishes and stores nothing.

`weather intel smoke` sends one synthetic (generated, never a camera) image with the Stage A
prompt to `llm.primary.longctx.mm` and prints metadata only: HTTP result, echoed model,
finish reason, latency and whether the reply passed shape validation. No retry, no database,
no Garage. Exit 0 only on an HTTP 200 with a completed reply.
"""

import asyncio
import io
import json
import os
import sys

from PIL import Image as PILImage
from PIL import ImageDraw

from . import validate
from .analyze import VISUAL_PROMPT
from .client import Cortex, CortexError, Image, check_base_url, load_key, request_body


def synthetic_image() -> Image:
    picture = PILImage.new("RGB", (640, 480), (120, 160, 210))
    draw = ImageDraw.Draw(picture)
    draw.rectangle((0, 360, 640, 480), fill=(70, 90, 60))
    draw.ellipse((420, 60, 580, 140), fill=(235, 235, 240))
    buffer = io.BytesIO()
    picture.save(buffer, "JPEG", quality=80)
    return Image("image/jpeg", buffer.getvalue())


async def smoke() -> int:
    key = load_key()
    if key is None:
        print(json.dumps({"result": "no_key"}))
        return 2
    cortex = Cortex(key, base_url=check_base_url(os.environ.get("WEATHER_CORTEX_BASE_URL")))
    context = {
        "task": "visual_analysis",
        "camera": "Synthetic test image; not a camera.",
        "segment": "daytime",
        "source_interval": {"start": "2026-01-01T12:00:00Z", "end": "2026-01-01T13:00:00Z"},
        "evidence": [{"id": "f-smoke-0-0", "kind": "frame", "source_time": "2026-01-01T12:30:00Z"}],
        "caveats": ["Synthetic image generated for a connectivity check."],
        "schema": validate.schema(),
    }
    body = request_body(
        VISUAL_PROMPT, json.dumps(context), [("f-smoke-0-0", synthetic_image())], max_tokens=1024
    )
    try:
        reply = await cortex.chat(body)
    except CortexError as error:
        print(json.dumps({"result": error.kind, "timeout": error.timeout}))
        return 1
    finally:
        await cortex.aclose()
    try:
        validate.visual(
            reply.content,
            reply.finish_reason,
            supplied=["f-smoke-0-0"],
            window=0,
            nonce="smoke",
        )
        validation = "accepted"
    except validate.Rejected as rejection:
        validation = rejection.code
    print(
        json.dumps(
            {
                "result": "http_200",
                "model": reply.model,
                "finish_reason": reply.finish_reason,
                "seconds": round(reply.seconds, 2),
                "validation": validation,
            }
        )
    )
    return 0 if reply.finish_reason in ("stop", "length") else 1


def main() -> None:
    sys.exit(asyncio.run(smoke()))
