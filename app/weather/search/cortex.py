# SPDX-License-Identifier: MIT
"""Cortex `embed.main` client: one call at a time, bounded, validated; never logs text or keys.

A profile binds the model route, dimensions and the query transform. Documents are embedded
unprefixed; a query is the profile's instruction prefix followed by the exact query text.
"""

import asyncio
import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx2

KEY_FILE = Path("/var/run/weather-cortex/api_key.txt")
NORM_TOLERANCE = 1e-3
MAX_RESPONSE_BYTES = 1024 * 1024
log = logging.getLogger("weather.search")


@dataclass(frozen=True)
class Profile:
    id: str
    model: str
    dimensions: int
    query_prefix: str


# Must match the rows seeded into weather.embedding_profile.
PROFILES = {
    profile.id: profile
    for profile in (
        Profile(
            "embed-main-2560-v1",
            "embed.main",
            2560,
            "Instruct: Given a web search query, retrieve relevant passages that answer the "
            "query\nQuery:",
        ),
    )
}
DEFAULT_PROFILE = "embed-main-2560-v1"


class EmbeddingUnavailable(RuntimeError):
    """The model route failed, timed out or returned an unusable vector (sanitized)."""

    def __init__(self, reason: str = "error") -> None:
        super().__init__("Embedding unavailable")
        self.reason = reason


def validate(data: Any, count: int, dimensions: int) -> list[list[float]]:
    """Exactly one finite, nonzero, unit-norm vector of the profile's size per input, in order."""
    if not isinstance(data, list) or len(data) != count:
        raise EmbeddingUnavailable("invalid")
    vectors = []
    for position, item in enumerate(data):
        if not isinstance(item, dict) or item.get("index") != position:
            raise EmbeddingUnavailable("invalid")
        values = item.get("embedding")
        if not isinstance(values, list) or len(values) != dimensions:
            raise EmbeddingUnavailable("invalid")
        if not all(isinstance(v, int | float) and not isinstance(v, bool) for v in values):
            raise EmbeddingUnavailable("invalid")
        vector = [float(v) for v in values]
        if not all(math.isfinite(v) for v in vector):
            raise EmbeddingUnavailable("invalid")
        norm = math.sqrt(math.fsum(v * v for v in vector))
        if norm == 0 or abs(norm - 1) > NORM_TOLERANCE:
            raise EmbeddingUnavailable("invalid")
        vectors.append(vector)
    return vectors


def vector_text(vector: Sequence[float]) -> str:
    """pgvector's text input form; SQL casts it, so no driver codec is involved."""
    return "[" + ",".join(repr(float(v)) for v in vector) + "]"


class Embedder:
    def __init__(
        self,
        base_url: str,
        key: str,
        profile: Profile,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self.profile = profile
        self._client = httpx2.AsyncClient(
            base_url=base_url.rstrip("/") + "/",
            headers={"Authorization": f"Bearer {key}"},
            follow_redirects=False,
            trust_env=False,
            limits=httpx2.Limits(max_connections=1, max_keepalive_connections=1),
            transport=transport,
        )
        # Inference is shared with other consumers: one call at a time per process.
        self._slot = asyncio.Semaphore(1)

    async def embed(self, texts: Sequence[str], *, timeout: float) -> list[list[float]]:
        """One attempt, bounded by `timeout` including the wait for the slot. No retry.

        Waiting out the budget behind another call is `busy`, not a Cortex failure.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(timeout, 0.001)
        try:
            async with asyncio.timeout_at(deadline):
                await self._slot.acquire()
        except TimeoutError:
            raise EmbeddingUnavailable("busy") from None
        try:
            async with asyncio.timeout_at(deadline):
                response = await self._client.post(
                    "embeddings",
                    json={"model": self.profile.model, "input": list(texts)},
                    timeout=httpx2.Timeout(max(deadline - loop.time(), 0.001)),
                )
                if response.status_code != 200 or len(response.content) > MAX_RESPONSE_BYTES:
                    raise EmbeddingUnavailable("error")
                body = response.json()
        except TimeoutError:
            raise EmbeddingUnavailable("timeout") from None
        except EmbeddingUnavailable:
            raise
        except Exception:
            # Never retain provider errors: they can echo request text or headers.
            raise EmbeddingUnavailable("error") from None
        finally:
            self._slot.release()
        data = body.get("data") if isinstance(body, dict) else None
        return validate(data, len(texts), self.profile.dimensions)

    async def query(self, text: str, *, timeout: float) -> list[float]:
        """The exact query text after the profile's prefix: no trim or normalization."""
        return (await self.embed([self.profile.query_prefix + text], timeout=timeout))[0]

    async def aclose(self) -> None:
        await self._client.aclose()


def load_embedder(
    base_url: str | None, profile_id: str, key_file: Path = KEY_FILE
) -> Embedder | None:
    """None when Cortex is not configured or its key is unreadable; search stays lexical."""
    if base_url is None:
        return None
    try:
        key = key_file.read_text().strip()
    except (OSError, UnicodeError):
        key = ""
    if not key:
        log.warning("Cortex key unavailable; semantic search disabled")
        return None
    return Embedder(base_url, key, PROFILES[profile_id])
