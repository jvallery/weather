# SPDX-License-Identifier: MIT
"""Weather #77 copy verdict with synthetic catalog and object-store responses."""

import importlib.util
import json
import os
import sys
import unittest
from collections import namedtuple
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
Row = namedtuple("Row", "id bucket asset_class object_key sha256 bytes content_type")


class MediaRestoreDriverTests(unittest.IsolatedAsyncioTestCase):
    async def test_withheld_control_and_unexpected_custody_miss(self):
        rows = [
            Row("keep-out", "weather-media", "still", "control", "digest", 1, "image/jpeg"),
            Row("restore", "weather-media", "still", "still", "digest", 1, "image/jpeg"),
            Row("extra", "weather-media", "video", "video", "digest", 1, "video/mp4"),
            Row("original", "weather-originals", "original", "original", "digest", 1, "image/jpeg"),
        ]
        for missing in (False, True):
            with self.subTest(missing=missing), TemporaryDirectory() as directory:
                root = Path(directory)
                ready = root / "ready"
                ready.touch()
                candidates = root / "candidates.json"
                candidates.write_text(
                    json.dumps(
                        [{"bucket": "weather-media", "object_key": "still", "created_epoch": 1}]
                    )
                )
                paths = {"/gdata/ready": ready, "/drill/rpo-candidates.json": candidates}

                marked = [rows[0], rows[2]] if missing else [rows[0]]
                connection = AsyncMock()
                connection.execute.side_effect = [
                    SimpleNamespace(all=lambda: rows),
                    SimpleNamespace(all=lambda marked=marked: marked),
                ]
                engine = MagicMock()
                engine.connect.return_value.__aenter__.return_value = connection
                engine.dispose = AsyncMock()
                store = SimpleNamespace(
                    put=AsyncMock(), head=AsyncMock(return_value=True), aclose=AsyncMock()
                )
                store_factory = MagicMock(return_value=store)
                store_factory.from_environment.return_value = store
                modules = {
                    "sqlalchemy": SimpleNamespace(text=str),
                    "weather.db.connection": SimpleNamespace(
                        create_database_engine=lambda engine=engine: engine,
                        wait_for_database=AsyncMock(),
                    ),
                    "weather.publish.objects": SimpleNamespace(
                        ObjectStore=store_factory, StoreError=Exception
                    ),
                }

                async def fetch(_store, key, _size, target, *, missing=missing):
                    self.assertNotEqual(key, "object-custody/weather-media/control")
                    if missing and key == "object-custody/weather-media/video":
                        return None
                    target.write_bytes(b"x")
                    return "digest"

                env = {
                    "DRILL_DAY": "2026-10-29",
                    "CUSTODY_REGION": "test",
                    "CUSTODY_ACCESS_KEY_ID": "synthetic",
                    "CUSTODY_SECRET_ACCESS_KEY": "synthetic",
                }
                spec = importlib.util.spec_from_file_location(
                    "media_restore_driver", ROOT / "deploy/acceptance/restore/media-drill/driver.py"
                )
                driver = importlib.util.module_from_spec(spec)
                with patch.dict(sys.modules, modules), patch.dict(os.environ, env):
                    spec.loader.exec_module(driver)
                    with (
                        patch.object(driver, "WORK", root),
                        patch.object(driver, "Path", side_effect=paths.__getitem__),
                        patch.object(driver, "fetch", side_effect=fetch),
                        patch.object(
                            driver.subprocess,
                            "run",
                            return_value=SimpleNamespace(stdout="{}", returncode=0),
                        ),
                        patch.object(driver, "log"),
                    ):
                        await driver.main()

                receipt = json.loads((root / "receipt.json").read_text())
                self.assertTrue(receipt["marked_unavailable_exactly_expected"])
                self.assertEqual(receipt["buckets"]["weather-media"]["withheld"], 1)
                self.assertEqual(
                    receipt["buckets"]["weather-media"]["custody_missing"], int(missing)
                )
                self.assertEqual(receipt["result"], "fail" if missing else "pass")


if __name__ == "__main__":
    unittest.main()
