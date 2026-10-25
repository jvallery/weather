# SPDX-License-Identifier: MIT
"""Cortex chat client for `llm.primary.longctx.mm`: the proven image path, nothing else.

The base URL is fixed to the Cortex route and the model to the pinned multimodal route (R21,
C9); there is no external model and no text-only fallback. Each request carries at most a few
MB of images (checked here, before any byte leaves), temperature 0.1 and a 120 s client
timeout. Errors are classified without retaining provider text: auth and media errors stop
retries, unavailability and 429 are retryable (honouring a bounded Retry-After). The key and
request/response bodies are never logged.
"""

import base64
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import httpx2

from .effective import public_model

CORTEX_BASE_URL = "https://cortex.vallery.net/v1"
MODEL_ROUTE = "llm.primary.longctx.mm"
KEY_FILE = Path("/var/run/weather-cortex/api_key.txt")
TIMEOUT_SECONDS = 120.0
TEMPERATURE = 0.1
MAX_IMAGE_BYTES = 1024 * 1024
MAX_REQUEST_IMAGE_BYTES = 4 * 1024 * 1024
MAX_IMAGES = 9
MAX_RESPONSE_BYTES = 256 * 1024
MAX_RETRY_AFTER = 60.0
Kind = Literal["unavailable", "busy", "auth", "media", "invalid"]


class CortexError(RuntimeError):
    """A sanitized failure: `kind` decides retry; nothing from the provider is kept."""

    def __init__(self, kind: Kind, *, retry_after: float | None = None, timeout: bool = False):
        super().__init__(f"Cortex {kind}")
        self.kind, self.retry_after, self.timeout = kind, retry_after, timeout

    @property
    def retryable(self) -> bool:
        return self.kind in ("unavailable", "busy")


def check_base_url(value: str | None) -> str:
    """Only the Cortex route is an inference endpoint."""
    if value is None or value.rstrip("/") != CORTEX_BASE_URL:
        raise ValueError("Inference base URL must be the Cortex route")
    return CORTEX_BASE_URL


@dataclass(frozen=True)
class Image:
    mime: Literal["image/jpeg", "image/png"]
    data: bytes

    def part(self) -> dict[str, Any]:
        encoded = base64.b64encode(self.data).decode()
        return {"type": "image_url", "image_url": {"url": f"data:{self.mime};base64,{encoded}"}}


@dataclass(frozen=True)
class Reply:
    content: str | None
    finish_reason: str | None
    model: str
    body: bytes
    seconds: float


def request_body(
    system: str, text: str, images: list[tuple[str, Image]], *, max_tokens: int
) -> dict[str, Any]:
    """The exact JSON sent: system prompt, validated context text, then labelled images."""
    if len(images) > MAX_IMAGES:
        raise CortexError("media")
    if (
        any(len(image.data) > MAX_IMAGE_BYTES for _, image in images)
        or sum(len(image.data) for _, image in images) > MAX_REQUEST_IMAGE_BYTES
    ):
        raise CortexError("media")
    content: list[dict[str, Any]] = [{"type": "text", "text": text}]
    for label, image in images:
        content.append({"type": "text", "text": f"Evidence {label}:"})
        content.append(image.part())
    return {
        "model": MODEL_ROUTE,
        "temperature": TEMPERATURE,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
        "chat_template_kwargs": {"enable_thinking": False},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ],
    }


def _retry_after(response: httpx2.Response) -> float | None:
    try:
        value = float(response.headers.get("retry-after", ""))
    except ValueError:
        return None
    return min(max(value, 0.0), MAX_RETRY_AFTER)


class Cortex:
    def __init__(
        self,
        key: str,
        *,
        base_url: str = CORTEX_BASE_URL,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx2.AsyncClient(
            base_url=check_base_url(base_url) + "/",
            headers={"Authorization": f"Bearer {key}"},
            follow_redirects=False,
            trust_env=False,
            limits=httpx2.Limits(max_connections=1, max_keepalive_connections=1),
            transport=transport,
        )

    async def chat(self, body: dict[str, Any]) -> Reply:
        """One attempt; the caller holds the global inference slot and owns retries."""
        started = time.monotonic()
        try:
            response = await self._client.post(
                "chat/completions",
                content=json.dumps(body, separators=(",", ":")).encode(),
                headers={"Content-Type": "application/json"},
                timeout=httpx2.Timeout(TIMEOUT_SECONDS, connect=10.0),
            )
        except httpx2.TimeoutException:
            raise CortexError("unavailable", timeout=True) from None
        except httpx2.HTTPError:
            raise CortexError("unavailable") from None
        status = response.status_code
        if status in (401, 403):
            raise CortexError("auth")
        if status == 429:
            raise CortexError("busy", retry_after=_retry_after(response))
        if status in (400, 413, 415, 422):
            raise CortexError("media")
        if status != 200:
            raise CortexError("unavailable", retry_after=_retry_after(response))
        raw = response.content
        if len(raw) > MAX_RESPONSE_BYTES:
            raise CortexError("invalid")
        try:
            data = json.loads(raw)
            choice = data["choices"][0]
            content = choice["message"].get("content")
            finish = choice.get("finish_reason")
            model = public_model(data.get("model"), MODEL_ROUTE)
        except ValueError, KeyError, IndexError, TypeError, AttributeError:
            raise CortexError("invalid") from None
        if content is not None and not isinstance(content, str):
            raise CortexError("invalid")
        return Reply(
            content=content,
            finish_reason=finish if isinstance(finish, str) else None,
            model=model,
            body=raw,
            seconds=time.monotonic() - started,
        )

    async def aclose(self) -> None:
        await self._client.aclose()


def load_key(key_file: Path = KEY_FILE) -> str | None:
    try:
        key = key_file.read_text().strip()
    except OSError, UnicodeError:
        return None
    return key or None
