# SPDX-License-Identifier: MIT
"""One bounded Digest snapshot exchange; no DNS, redirects or endpoint logging."""

import asyncio
import hashlib
import ipaddress
import re
import secrets
import socket
import ssl
import time
from dataclasses import dataclass
from urllib.request import parse_http_list, parse_keqv_list

from .config import CaptureConfig

SNAPSHOT_PATH = "/cgi-bin/snapshot.cgi?channel=1"


class CaptureFailure(Exception):
    def __init__(self, reason: str, status: int | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status = status


@dataclass(frozen=True)
class Snapshot:
    body: bytes
    content_type: str


class Digest:
    """RFC 7616 auth with a fresh cnonce and bounded challenge fields."""

    def __init__(self, username: str, password: str) -> None:
        self._username, self._password = username, password
        self._challenge: dict[str, str] | None = None
        self._count = 0

    def challenge(self, header: str) -> None:
        if not header.lower().startswith("digest ") or len(header) > 8192:
            raise CaptureFailure("authentication", 401)
        values = parse_keqv_list(parse_http_list(header[7:]))
        if not values.get("realm") or not values.get("nonce"):
            raise CaptureFailure("authentication", 401)
        algorithm = values.get("algorithm", "MD5").upper()
        if algorithm not in {"MD5", "MD5-SESS", "SHA-256", "SHA-256-SESS"}:
            raise CaptureFailure("authentication", 401)
        if "qop" in values and "auth" not in [x.strip() for x in values["qop"].split(",")]:
            raise CaptureFailure("authentication", 401)
        if any(c in value for value in values.values() for c in ["\r", "\n", "\x00"]):
            raise CaptureFailure("authentication", 401)
        self._challenge, self._count = values, 0

    def header(self) -> str | None:
        if self._challenge is None:
            return None
        values = self._challenge
        algorithm = values.get("algorithm", "MD5").upper()
        hash_name = "md5" if algorithm.startswith("MD5") else "sha256"

        def digest(text: str) -> str:
            return hashlib.new(hash_name, text.encode(), usedforsecurity=False).hexdigest()

        self._count += 1
        nc, cnonce = f"{self._count:08x}", secrets.token_hex(16)
        ha1 = digest(f"{self._username}:{values['realm']}:{self._password}")
        if algorithm.endswith("-SESS"):
            ha1 = digest(f"{ha1}:{values['nonce']}:{cnonce}")
        ha2 = digest(f"GET:{SNAPSHOT_PATH}")
        qop = "auth" if "qop" in values else None
        middle = f"{nc}:{cnonce}:{qop}:" if qop else ""
        response = digest(f"{ha1}:{values['nonce']}:{middle}{ha2}")

        def quoted(value: str) -> str:
            return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

        fields = {
            "username": self._username,
            "realm": values["realm"],
            "nonce": values["nonce"],
            "uri": SNAPSHOT_PATH,
            "response": response,
        }
        if "opaque" in values:
            fields["opaque"] = values["opaque"]
        result = "Digest " + ", ".join(f"{k}={quoted(v)}" for k, v in fields.items())
        result += f", algorithm={algorithm}"
        if qop:
            result += f", qop=auth, nc={nc}, cnonce={quoted(cnonce)}"
        elif algorithm.endswith("-SESS"):
            result += f", cnonce={quoted(cnonce)}"
        return result


class SnapshotClient:
    def __init__(self, config: CaptureConfig) -> None:
        self.config = config
        self._digest = Digest(config.username, config.password)
        self._last_exchange_ns: int | None = None

    async def fetch(self) -> Snapshot:
        try:
            # Includes both Digest exchanges, response headers and every body byte.
            async with asyncio.timeout(self.config.total_seconds):
                for attempt in range(2):
                    if self._last_exchange_ns is not None:
                        delay = (
                            self._last_exchange_ns
                            + self.config.cadence_ms * 1_000_000
                            - time.monotonic_ns()
                        ) / 1e9
                        if delay > 0:
                            await asyncio.sleep(delay)
                    self._last_exchange_ns = time.monotonic_ns()
                    result = await self._exchange(self._digest.header())
                    if isinstance(result, str):
                        if attempt:
                            raise CaptureFailure("authentication", 401)
                        self._digest.challenge(result)
                    else:
                        return result
                raise CaptureFailure("authentication", 401)
        except CaptureFailure:
            raise
        except TimeoutError:
            raise CaptureFailure("timeout") from None
        except OSError, EOFError, asyncio.IncompleteReadError, asyncio.LimitOverrunError:
            raise CaptureFailure("transport") from None
        except ValueError, UnicodeError:
            raise CaptureFailure("protocol") from None

    async def _exchange(self, authorization: str | None) -> Snapshot | str:
        context = ssl.create_default_context() if self.config.https else None
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(
                self.config.host,
                self.config.port,
                ssl=context,
                server_hostname=self.config.host if context else None,
                limit=32768,
                family=socket.AF_INET
                if ipaddress.ip_address(self.config.host).version == 4
                else socket.AF_INET6,
                flags=socket.AI_NUMERICHOST,
            ),
            self.config.connect_seconds,
        )
        try:
            # The address is connection configuration, never interpolated into a URL.
            host_header = self.config.host
            if ":" in host_header:
                host_header = f"[{host_header}]"
            headers = [
                f"GET {SNAPSHOT_PATH} HTTP/1.1",
                f"Host: {host_header}",
                "Accept: image/jpeg",
                "Connection: close",
            ]
            if authorization:
                headers.append("Authorization: " + authorization)
            writer.write(("\r\n".join(headers) + "\r\n\r\n").encode())
            await asyncio.wait_for(writer.drain(), self.config.read_seconds)
            raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), self.config.read_seconds)
            if len(raw) > 32768:
                raise CaptureFailure("protocol")
            lines = raw.decode("latin-1").split("\r\n")
            version, status_text, *_ = lines[0].split(" ")
            if version not in {"HTTP/1.0", "HTTP/1.1"}:
                raise CaptureFailure("protocol")
            status = int(status_text)
            response_headers: dict[str, str] = {}
            for line in lines[1:-2]:
                name, value = line.split(":", 1)
                name = name.lower().strip()
                if name in response_headers:
                    raise CaptureFailure("protocol")
                response_headers[name] = value.strip()
            if status == 401:
                return response_headers.get("www-authenticate", "")
            if status != 200:
                raise CaptureFailure("redirect" if 300 <= status < 400 else "http_status", status)
            mime = response_headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if mime != "image/jpeg":
                raise CaptureFailure("content_type", status)
            if response_headers.get("content-encoding", "identity").lower() != "identity":
                raise CaptureFailure("protocol", status)
            body = bytearray()
            transfer = response_headers.get("transfer-encoding", "").lower()
            length_text = response_headers.get("content-length")
            if transfer and length_text is not None:
                raise CaptureFailure("protocol", status)
            if transfer:
                if transfer != "chunked":
                    raise CaptureFailure("protocol", status)
                while True:
                    chunk_line = await asyncio.wait_for(
                        reader.readuntil(b"\r\n"), self.config.read_seconds
                    )
                    if len(chunk_line) > 128:
                        raise CaptureFailure("protocol", status)
                    size_text = chunk_line.strip().split(b";", 1)[0]
                    if not re.fullmatch(rb"[0-9A-Fa-f]{1,16}", size_text):
                        raise CaptureFailure("protocol", status)
                    size = int(size_text, 16)
                    if size == 0:
                        # Bound trailers as strictly as initial headers.
                        trailer_bytes = 0
                        while True:
                            trailer = await asyncio.wait_for(
                                reader.readuntil(b"\r\n"), self.config.read_seconds
                            )
                            trailer_bytes += len(trailer)
                            if trailer_bytes > 8192:
                                raise CaptureFailure("protocol", status)
                            if trailer == b"\r\n":
                                break
                        break
                    self._check_size(len(body) + size)
                    await self._read_body(reader, body, size)
                    if (
                        await asyncio.wait_for(reader.readexactly(2), self.config.read_seconds)
                        != b"\r\n"
                    ):
                        raise CaptureFailure("protocol", status)
            elif length_text is not None:
                length = int(length_text)
                if length < 0:
                    raise CaptureFailure("protocol", status)
                self._check_size(length)
                await self._read_body(reader, body, length)
            else:
                while True:
                    chunk = await asyncio.wait_for(reader.read(65536), self.config.read_seconds)
                    if not chunk:
                        break
                    self._check_size(len(body) + len(chunk))
                    body.extend(chunk)
            return Snapshot(bytes(body), mime)
        finally:
            writer.close()
            # No await outside the overall deadline; closing interrupts a cancelled body read.

    def _check_size(self, size: int) -> None:
        if size > self.config.max_bytes:
            raise CaptureFailure("oversized", 200)

    async def _read_body(
        self,
        reader: asyncio.StreamReader,
        target: bytearray,
        size: int,
    ) -> None:
        while size:
            part = await asyncio.wait_for(reader.read(min(size, 65536)), self.config.read_seconds)
            if not part:
                raise CaptureFailure("truncated", 200)
            target.extend(part)
            size -= len(part)
