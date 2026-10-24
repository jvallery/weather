# SPDX-License-Identifier: MIT
"""Publication rules without services: signing, product validation, privacy, store errors."""

import asyncio
import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx2
import pytest
from weather.publish.model import (
    Product,
    ProductError,
    ProductFile,
    StagedObject,
    check_public_document,
    product_from_manifest,
    product_manifest,
    role_class,
)
from weather.publish.objects import ObjectInfo, ObjectStore, StoreError, sign
from weather.publish.publisher import ObjectConflict, ReadbackFailed, describe, upload, verify

END = datetime(2026, 10, 24, 0, 30, tzinfo=UTC)
START = END - timedelta(hours=11)
EMBARGO = END + timedelta(seconds=630)
EMPTY = hashlib.sha256(b"").hexdigest()


def video(
    state: str = "published", roles: tuple[str, ...] | None = None, **changes: object
) -> Product:
    names = ("native", "web", "mobile", "poster") if roles is None else roles
    files = tuple(
        ProductFile(
            role=role,
            extension="jpg" if role == "poster" else "mp4",
            data=None,
            path=Path("/nonexistent") / role,
            width=1920,
            height=870,
        )
        for role in names
    )
    values: dict[str, object] = {
        "camera_id": "skycam_east",
        "cycle_date": date(2026, 10, 23),
        "segment": "daytime",
        "starts_at": START,
        "ends_at": END,
        "frames": 4200,
        "public_not_before": EMBARGO,
        "state": state,
        "reason": None if state == "published" else "low_coverage",
        "files": files,
    }
    values.update(changes)
    return Product.video(**values)  # type: ignore[arg-type]


def test_aws_signature_v4_vectors() -> None:
    # The two GET examples of the AWS S3 Signature Version 4 documentation.
    common = {
        "host": "examplebucket.s3.amazonaws.com",
        "payload_sha256": EMPTY,
        "access_key": "AKIAIOSFODNN7EXAMPLE",
        "secret_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        "region": "us-east-1",
        "now": datetime(2013, 5, 24, tzinfo=UTC),
    }
    get = sign(method="GET", path="/test.txt", query=[], headers={"Range": "bytes=0-9"}, **common)  # type: ignore[arg-type]
    assert get["authorization"].endswith(
        "SignedHeaders=host;range;x-amz-content-sha256;x-amz-date,"
        " Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"
    )
    lifecycle = sign(method="GET", path="/", query=[("lifecycle", "")], headers={}, **common)  # type: ignore[arg-type]
    assert lifecycle["authorization"].endswith(
        "Signature=fea454ca298b7da1c68078a5d1bdbfbbe0d65c699e0f91ac7a200a0136783543"
    )


def test_products_fail_before_upload() -> None:
    video().validate()
    video(
        "unavailable", roles=(), files=(ProductFile.sidecar({"state": "unavailable"}),)
    ).validate()
    for bad in (
        video(public_not_before=END + timedelta(seconds=629)),
        video(roles=("native", "web", "poster")),
        video("partial", reason=None),
        video(reason="low_coverage"),
        video("unavailable"),
        video(frames=7201),
        video(starts_at=END),
    ):
        with pytest.raises(ProductError):
            bad.validate()
    # Originals belong only to stills, under their own role and extension.
    assert role_class("still", "original") == "original"
    assert role_class("video", "poster_960") == "poster"
    for product, role in (("video", "original"), ("keogram", "original"), ("video", "native_x")):
        with pytest.raises(ProductError):
            role_class(product, role)
    on_disk = ProductFile(role="sidecar", extension="json", path=Path("/nonexistent"))
    with pytest.raises(ProductError):
        video(files=(*video().files, on_disk)).validate()
    with pytest.raises(ProductError):
        Product.still(
            camera_id="skycam_east",
            slot_start=datetime(2026, 10, 23, 18, 30, tzinfo=UTC),
            source_start=START,
            source_end=END,
            public_not_before=EMBARGO,
            files=(),
        )


def test_sidecars_never_name_private_storage() -> None:
    check_public_document({"time_map": [{"frame": 1}], "input_manifest_hash": "a" * 64})
    for bad in (
        {"bucket": "weather-media"},
        {"nested": [{"object_key": "video/x.mp4"}]},
        {"recipe": {"config_hash": "x"}},
        {"original_asset_id": "x"},
        {"note": "copied from weather-originals"},
        {"note": "original/" + "0" * 64 + ".jpg"},
    ):
        with pytest.raises(ProductError):
            ProductFile.sidecar(bad)
        # Building the file by hand does not bypass the check.
        raw = ProductFile(role="sidecar", extension="json", data=json.dumps(bad).encode())
        with pytest.raises(ProductError):
            video(files=(*video().files, raw)).validate()


def test_manifest_round_trip_keeps_everything_reconcile_needs() -> None:
    product = video()
    objects = (StagedObject("native", "video", "a" * 64, 10, "mp4", 1920, 870),)
    restored, items = product_from_manifest(
        json.loads(json.dumps(product_manifest(product, objects)))
    )
    assert items == objects
    assert restored.key == "2026-10-23/daytime"
    assert (restored.public_not_before, restored.source_end, restored.frames) == (
        EMBARGO,
        END,
        4200,
    )


class FakeStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.puts: list[str] = []

    async def head(self, key: str) -> ObjectInfo | None:
        data = self.objects.get(key)
        return None if data is None else ObjectInfo(len(data), hashlib.sha256(data).hexdigest())

    async def read(self, key: str, *, keep: bool = False) -> tuple[int, str, bytes | None]:
        data = self.objects[key]
        return len(data), hashlib.sha256(data).hexdigest(), data if keep else None

    async def put(self, key: str, *, data: bytes | None = None, **_: object) -> bool:
        self.puts.append(key)
        assert data is not None
        self.objects[key] = data
        return True


def test_keys_come_from_local_hashes_and_conflicts_are_never_overwritten() -> None:
    async def check() -> None:
        store = FakeStore()
        product = Product.keogram(
            camera_id="skycam_east",
            cycle_date=date(2026, 10, 23),
            segment="daytime",
            source_start=START,
            source_end=END,
            public_not_before=EMBARGO,
            files=(ProductFile.sidecar({"bins": 1440}),),
        )
        file = product.files[0]
        item = await describe(product, file)
        assert file.data is not None
        assert item.key == f"sidecar/{hashlib.sha256(file.data).hexdigest()}.json"
        await upload(store, item, file)  # type: ignore[arg-type]
        assert await verify(store, item, keep=True) == file.data  # type: ignore[arg-type]
        # The same bytes again: no second PUT.
        await upload(store, item, file)  # type: ignore[arg-type]
        assert store.puts == [item.key]
        # Different bytes under the key: fail loudly and leave the stored object alone.
        store.objects[item.key] = b"tampered"
        with pytest.raises(ObjectConflict):
            await upload(store, item, file)  # type: ignore[arg-type]
        assert store.objects[item.key] == b"tampered" and store.puts == [item.key]
        with pytest.raises(ReadbackFailed):
            await verify(store, item)  # type: ignore[arg-type]

    asyncio.run(check())


def test_failed_multipart_upload_is_aborted(tmp_path: Path) -> None:
    calls: list[tuple[str, str]] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        query = request.url.query.decode()
        calls.append((request.method, query))
        assert request.headers["authorization"].startswith("AWS4-HMAC-SHA256 Credential=")
        if request.method == "POST" and query == "uploads=":
            return httpx2.Response(
                200,
                content=b"<InitiateMultipartUploadResult><UploadId>u1</UploadId>"
                b"</InitiateMultipartUploadResult>",
            )
        if request.method == "PUT" and query.startswith("partNumber=1&"):
            return httpx2.Response(200, headers={"etag": '"e1"'})
        if request.method == "PUT":
            return httpx2.Response(500)
        if request.method == "DELETE":
            return httpx2.Response(204)
        raise AssertionError(request.method)

    async def check() -> None:
        path = tmp_path / "large.mp4"
        path.write_bytes(b"x" * (11 * 1024 * 1024))
        store = ObjectStore(
            endpoint="http://store.invalid",
            bucket="weather-media",
            access_key="test",
            secret_key="test",
            client=httpx2.AsyncClient(transport=httpx2.MockTransport(handle)),
            part_size=5 * 1024 * 1024,
            multipart_threshold=6 * 1024 * 1024,
        )
        with pytest.raises(StoreError) as failure:
            await store.put(
                "video/" + "a" * 64 + ".mp4",
                sha256="a" * 64,
                size=path.stat().st_size,
                content_type="video/mp4",
                path=path,
            )
        assert str(failure.value) == "Object store multipart part failed (500)"
        assert calls[-1] == ("DELETE", "uploadId=u1")
        await store.aclose()

    asyncio.run(check())


def test_multipart_never_completes_bytes_that_differ_from_the_key(tmp_path: Path) -> None:
    calls: list[tuple[str, str]] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        query = request.url.query.decode()
        calls.append((request.method, query))
        if query == "uploads=":
            return httpx2.Response(200, content=b"<R><UploadId>u2</UploadId></R>")
        if request.method == "PUT":
            return httpx2.Response(200, headers={"etag": '"e"'})
        if request.method == "DELETE":
            return httpx2.Response(204)
        raise AssertionError("CompleteMultipartUpload must not be sent")

    async def check() -> None:
        path = tmp_path / "changed.mp4"
        path.write_bytes(b"y" * (11 * 1024 * 1024))
        store = ObjectStore(
            endpoint="http://store.invalid",
            bucket="weather-media",
            access_key="test",
            secret_key="test",
            client=httpx2.AsyncClient(transport=httpx2.MockTransport(handle)),
            part_size=5 * 1024 * 1024,
            multipart_threshold=6 * 1024 * 1024,
        )
        # The local hash was taken before the file changed on disk.
        with pytest.raises(StoreError, match="multipart content changed"):
            await store.put(
                "video/" + "b" * 64 + ".mp4",
                sha256="b" * 64,
                size=path.stat().st_size,
                content_type="video/mp4",
                path=path,
            )
        assert calls[-1] == ("DELETE", "uploadId=u2")
        assert [method for method, _ in calls].count("PUT") == 3
        await store.aclose()

    asyncio.run(check())


def test_multipart_completion_is_conditional(tmp_path: Path) -> None:
    calls: list[tuple[str, str, str | None]] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        query = request.url.query.decode()
        calls.append((request.method, query, request.headers.get("if-none-match")))
        if query == "uploads=":
            return httpx2.Response(200, content=b"<R><UploadId>u3</UploadId></R>")
        if request.method == "PUT":
            return httpx2.Response(200, headers={"etag": '"e"'})
        if request.method == "POST":
            return httpx2.Response(412)
        return httpx2.Response(204)

    async def check() -> None:
        path = tmp_path / "exists.mp4"
        data = b"z" * (11 * 1024 * 1024)
        path.write_bytes(data)
        store = ObjectStore(
            endpoint="http://store.invalid",
            bucket="weather-media",
            access_key="test",
            secret_key="test",
            client=httpx2.AsyncClient(transport=httpx2.MockTransport(handle)),
            part_size=5 * 1024 * 1024,
            multipart_threshold=6 * 1024 * 1024,
        )
        sha = hashlib.sha256(data).hexdigest()
        created = await store.put(
            f"video/{sha}.mp4", sha256=sha, size=len(data), content_type="video/mp4", path=path
        )
        # Another writer completed the key first: report it, and abort this upload.
        assert created is False
        assert ("POST", "uploadId=u3", "*") in calls
        assert calls[-1][:2] == ("DELETE", "uploadId=u3")
        await store.aclose()

    asyncio.run(check())
