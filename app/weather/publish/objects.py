# SPDX-License-Identifier: MIT
"""A minimal S3 client for Garage: SigV4, conditional create, multipart with abort, readback.

Each bucket has its own key, so one ObjectStore serves one bucket. Errors carry the operation
and HTTP status only, never a URL, header, credential or response body.
"""

import asyncio
import hashlib
import hmac
import os
import re
import xml.etree.ElementTree as ElementTree
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import httpx2

EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
SHA256 = re.compile(r"[0-9a-f]{64}")
READ_CHUNK = 1024 * 1024
DEFAULT_PART = 16 * 1024 * 1024
DEFAULT_THRESHOLD = 64 * 1024 * 1024
MIN_PART = 5 * 1024 * 1024
CREDENTIAL_ROOT = Path("/var/run/weather-garage")
DEFAULT_ENDPOINT = "https://s3-hdd.vallery.net"


class StoreError(RuntimeError):
    """Transport or HTTP failure; safe to log."""

    def __init__(self, operation: str, status: int | None = None) -> None:
        super().__init__(f"Object store {operation} failed" + (f" ({status})" if status else ""))
        self.operation = operation
        self.status = status


class _Exists(Exception):
    """A conditional create lost to an existing object."""


@dataclass(frozen=True)
class ObjectInfo:
    size: int
    sha256: str | None


@dataclass(frozen=True)
class MultipartUpload:
    key: str
    upload_id: str
    initiated: datetime


class ObjectSpan:
    """An open GET whose bytes are counted: a short or long body raises StoreError."""

    def __init__(self, response: httpx2.Response, length: int) -> None:
        self._response, self.length = response, length

    async def chunks(self, size: int) -> AsyncIterator[bytes]:
        remaining = self.length
        try:
            async for chunk in self._response.aiter_raw(size):
                remaining -= len(chunk)
                if remaining < 0:
                    raise StoreError("span length")
                yield chunk
        except httpx2.HTTPError:
            raise StoreError("span read") from None
        if remaining:
            raise StoreError("span length")

    async def aclose(self) -> None:
        await self._response.aclose()


def _hmac(key: bytes, message: str) -> bytes:
    return hmac.digest(key, message.encode(), "sha256")


def _encode(value: str, safe: str = "-_.~") -> str:
    return quote(value, safe=safe)


def sign(
    *,
    method: str,
    host: str,
    path: str,
    query: list[tuple[str, str]],
    headers: dict[str, str],
    payload_sha256: str,
    access_key: str,
    secret_key: str,
    region: str,
    now: datetime,
) -> dict[str, str]:
    """AWS Signature Version 4 for S3; returns the headers to send, Authorization included."""
    amz_date = now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    day = amz_date[:8]
    signed = {key.lower(): " ".join(value.split()) for key, value in headers.items()}
    signed.update({"host": host, "x-amz-date": amz_date, "x-amz-content-sha256": payload_sha256})
    names = sorted(signed)
    canonical = "\n".join(
        [
            method,
            _encode(path, "/-_.~"),
            "&".join(f"{_encode(k)}={_encode(v)}" for k, v in sorted(query)),
            "".join(f"{name}:{signed[name]}\n" for name in names),
            ";".join(names),
            payload_sha256,
        ]
    )
    scope = f"{day}/{region}/s3/aws4_request"
    to_sign = "\n".join(
        ["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()]
    )
    key = _hmac(("AWS4" + secret_key).encode(), day)
    for part in (region, "s3", "aws4_request"):
        key = _hmac(key, part)
    signature = hmac.digest(key, to_sign.encode(), "sha256").hex()
    signed["authorization"] = (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
        f"SignedHeaders={';'.join(names)}, Signature={signature}"
    )
    return signed


def _xml(body: bytes) -> ElementTree.Element:
    root = ElementTree.fromstring(body)
    for element in root.iter():
        element.tag = element.tag.rsplit("}", 1)[-1]
    return root


async def _file_chunks(
    path: Path, start: int = 0, length: int | None = None
) -> AsyncIterator[bytes]:
    remaining = length
    with path.open("rb") as handle:
        await asyncio.to_thread(handle.seek, start)
        while remaining is None or remaining > 0:
            size = READ_CHUNK if remaining is None else min(READ_CHUNK, remaining)
            chunk = await asyncio.to_thread(handle.read, size)
            if not chunk:
                return
            if remaining is not None:
                remaining -= len(chunk)
            yield chunk


class ObjectStore:
    def __init__(
        self,
        *,
        endpoint: str,
        bucket: str,
        access_key: str,
        secret_key: str,
        region: str = "garage",
        client: httpx2.AsyncClient | None = None,
        part_size: int = DEFAULT_PART,
        multipart_threshold: int = DEFAULT_THRESHOLD,
    ) -> None:
        parts = urlsplit(endpoint)
        if parts.scheme not in {"https", "http"} or not parts.netloc or parts.path not in {"", "/"}:
            raise ValueError("Invalid object store endpoint")
        if part_size < MIN_PART or multipart_threshold < part_size:
            raise ValueError("Invalid multipart sizes")
        self.bucket = bucket
        self._origin = f"{parts.scheme}://{parts.netloc}"
        self._host = parts.netloc
        self._access, self._secret, self._region = access_key, secret_key, region
        self._part_size, self._threshold = part_size, multipart_threshold
        self._client = client or httpx2.AsyncClient(
            timeout=httpx2.Timeout(60.0, connect=10.0), follow_redirects=False
        )

    @classmethod
    def from_environment(
        cls, bucket: str, client: httpx2.AsyncClient | None = None
    ) -> ObjectStore:
        """Per-bucket key files mounted from KSOPS; endpoint and region are plain config."""
        directory = Path(os.environ.get("GARAGE_CREDENTIAL_DIRECTORY", CREDENTIAL_ROOT)) / bucket
        try:
            access = (directory / "access_key_id").read_text().strip()
            secret = (directory / "secret_access_key").read_text().strip()
        except OSError:
            raise StoreError("credential load") from None
        if not access or not secret:
            raise StoreError("credential load")
        return cls(
            endpoint=os.environ.get("GARAGE_ENDPOINT", DEFAULT_ENDPOINT),
            region=os.environ.get("GARAGE_REGION", "garage"),
            bucket=bucket,
            access_key=access,
            secret_key=secret,
            client=client,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _request(
        self,
        method: str,
        key: str | None,
        *,
        query: list[tuple[str, str]] | None = None,
        headers: dict[str, str] | None = None,
        payload_sha256: str = EMPTY_SHA256,
    ) -> tuple[str, dict[str, str]]:
        path = f"/{self.bucket}" + (f"/{key}" if key is not None else "")
        query = query or []
        signed = sign(
            method=method,
            host=self._host,
            path=path,
            query=query,
            headers=headers or {},
            payload_sha256=payload_sha256,
            access_key=self._access,
            secret_key=self._secret,
            region=self._region,
            now=datetime.now(UTC),
        )
        url = self._origin + _encode(path, "/-_.~")
        if query:
            url += "?" + "&".join(f"{_encode(k)}={_encode(v)}" for k, v in sorted(query))
        return url, signed

    async def _send(
        self,
        operation: str,
        method: str,
        key: str | None,
        *,
        ok: set[int],
        content: Any = None,
        **kwargs: Any,
    ) -> httpx2.Response:
        url, headers = self._request(method, key, **kwargs)
        try:
            response = await self._client.request(method, url, headers=headers, content=content)
        except httpx2.HTTPError:
            raise StoreError(operation) from None
        if response.status_code not in ok:
            raise StoreError(operation, response.status_code)
        return response

    async def head(self, key: str) -> ObjectInfo | None:
        response = await self._send("head", "HEAD", key, ok={200, 404})
        if response.status_code == 404:
            return None
        stored = response.headers.get("x-amz-meta-sha256")
        try:
            size = int(response.headers["content-length"])
        except KeyError, ValueError:
            raise StoreError("head") from None
        return ObjectInfo(size=size, sha256=stored if stored and SHA256.fullmatch(stored) else None)

    async def read(self, key: str, *, keep: bool = False) -> tuple[int, str, bytes | None]:
        """Stream the object and hash it locally; keep the bytes only for small sidecars."""
        url, headers = self._request("GET", key)
        digest, size, kept = hashlib.sha256(), 0, bytearray()
        try:
            async with self._client.stream("GET", url, headers=headers) as response:
                if response.status_code != 200:
                    raise StoreError("read", response.status_code)
                async for chunk in response.aiter_bytes():
                    digest.update(chunk)
                    size += len(chunk)
                    if keep:
                        kept.extend(chunk)
        except httpx2.HTTPError:
            raise StoreError("read") from None
        return size, digest.hexdigest(), bytes(kept) if keep else None

    async def open_span(self, key: str, first: int, last: int, size: int) -> ObjectSpan:
        """Start a GET of bytes `first`..`last` (inclusive) of an object of `size` bytes.

        The status, length, Content-Range and identity encoding are checked before any byte
        is returned, so a missing, resized or re-encoded object fails here. Close the span.
        """
        if not 0 <= first <= last < size:
            raise ValueError("Invalid object span")
        whole = first == 0 and last == size - 1
        url, headers = self._request(
            "GET", key, headers={} if whole else {"range": f"bytes={first}-{last}"}
        )
        headers["accept-encoding"] = "identity"
        try:
            response = await self._client.send(
                self._client.build_request("GET", url, headers=headers), stream=True
            )
        except httpx2.HTTPError:
            raise StoreError("span") from None
        expected = {
            "content-length": str(last - first + 1),
            "content-range": None if whole else f"bytes {first}-{last}/{size}",
            "content-encoding": None,
        }
        if response.status_code != (200 if whole else 206) or any(
            response.headers.get(name) != value for name, value in expected.items()
        ):
            await response.aclose()
            raise StoreError("span", response.status_code)
        return ObjectSpan(response, last - first + 1)

    async def put(
        self,
        key: str,
        *,
        sha256: str,
        size: int,
        content_type: str,
        path: Path | None = None,
        data: bytes | None = None,
    ) -> bool:
        """Create the object unless it exists. Returns False when a concurrent writer won."""
        headers = {
            "content-type": content_type,
            "x-amz-meta-sha256": sha256,
        }
        if path is not None and size > self._threshold:
            return await self._multipart(key, path=path, size=size, sha256=sha256, headers=headers)
        if (path is None) == (data is None):
            raise ValueError("put takes a path or data")
        body: Any = data if data is not None else _file_chunks(path)  # type: ignore[arg-type]
        # The signed payload hash binds the bytes: the store rejects anything else.
        url, signed = self._request(
            "PUT", key, headers={**headers, "if-none-match": "*"}, payload_sha256=sha256
        )
        # Explicit length, so a streamed file is not sent chunked.
        signed["content-length"] = str(size)
        try:
            response = await self._client.request("PUT", url, headers=signed, content=body)
        except httpx2.HTTPError:
            raise StoreError("put") from None
        if response.status_code not in {200, 412}:
            raise StoreError("put", response.status_code)
        return response.status_code == 200

    async def _multipart(
        self, key: str, *, path: Path, size: int, sha256: str, headers: dict[str, str]
    ) -> bool:
        response = await self._send(
            "multipart start", "POST", key, ok={200}, query=[("uploads", "")], headers=headers
        )
        upload_id = _xml(response.content).findtext("UploadId")
        if not upload_id:
            raise StoreError("multipart start")
        try:
            parts: list[tuple[int, str]] = []
            whole = hashlib.sha256()
            for number, offset in enumerate(range(0, size, self._part_size), start=1):
                length = min(self._part_size, size - offset)
                chunk = b"".join([part async for part in _file_chunks(path, offset, length)])
                if len(chunk) != length:
                    raise StoreError("multipart read")
                whole.update(chunk)
                part = await self._send(
                    "multipart part",
                    "PUT",
                    key,
                    ok={200},
                    content=chunk,
                    query=[("partNumber", str(number)), ("uploadId", upload_id)],
                    payload_sha256=hashlib.sha256(chunk).hexdigest(),
                )
                etag = part.headers.get("etag")
                if not etag:
                    raise StoreError("multipart part")
                parts.append((number, etag))
            # Parts are signed as read; the whole must still be the bytes the key names, or
            # the upload is aborted instead of completing wrong bytes under the address.
            if whole.hexdigest() != sha256:
                raise StoreError("multipart content changed")
            body = (
                "<CompleteMultipartUpload>"
                + "".join(
                    f"<Part><PartNumber>{n}</PartNumber><ETag>{e}</ETag></Part>" for n, e in parts
                )
                + "</CompleteMultipartUpload>"
            ).encode()
            # Conditional completion, like the single PUT: never replace an existing object.
            done = await self._send(
                "multipart complete",
                "POST",
                key,
                ok={200, 412},
                content=body,
                query=[("uploadId", upload_id)],
                headers={"if-none-match": "*"},
                payload_sha256=hashlib.sha256(body).hexdigest(),
            )
            if done.status_code == 412:
                raise _Exists()
            if _xml(done.content).tag == "Error":
                raise StoreError("multipart complete")
        except _Exists:
            await asyncio.shield(self.abort_multipart(key, upload_id))
            return False
        except BaseException:
            # Never leave an incomplete upload behind, even on cancellation.
            await asyncio.shield(self.abort_multipart(key, upload_id))
            raise
        return True

    async def abort_multipart(self, key: str, upload_id: str) -> None:
        try:
            await self._send(
                "multipart abort",
                "DELETE",
                key,
                ok={204, 200, 404},
                query=[("uploadId", upload_id)],
            )
        except StoreError:
            # The reconciler aborts stale uploads that a failed abort leaves behind.
            pass

    async def list_multipart(self) -> list[MultipartUpload]:
        uploads: list[MultipartUpload] = []
        marker: list[tuple[str, str]] = []
        for _ in range(100):
            response = await self._send(
                "multipart list", "GET", None, ok={200}, query=[("uploads", ""), *marker]
            )
            root = _xml(response.content)
            for item in root.iter("Upload"):
                key, upload_id = item.findtext("Key"), item.findtext("UploadId")
                initiated = item.findtext("Initiated")
                if key and upload_id and initiated:
                    uploads.append(
                        MultipartUpload(
                            key, upload_id, datetime.fromisoformat(initiated.replace("Z", "+00:00"))
                        )
                    )
            if root.findtext("IsTruncated") != "true":
                return uploads
            marker = [
                ("key-marker", root.findtext("NextKeyMarker") or ""),
                ("upload-id-marker", root.findtext("NextUploadIdMarker") or ""),
            ]
        return uploads
