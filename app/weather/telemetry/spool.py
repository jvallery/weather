# SPDX-License-Identifier: MIT
"""One SQLite WAL/FULL commit per datagram; canonical receipt IDs survive replay."""

import contextlib
import fcntl
import hashlib
import os
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .parse import MAX_DATAGRAM_BYTES


class SpoolError(RuntimeError):
    pass


@dataclass(frozen=True)
class Receipt:
    id: uuid.UUID
    receiver_incarnation: str
    received_ns: int
    raw: bytes = field(repr=False)
    sha256: str
    quarantine_reason: str | None = None

    @classmethod
    def create(
        cls,
        raw: bytes,
        incarnation: str,
        received_ns: int,
        *,
        receipt_id: uuid.UUID | None = None,
        quarantine_reason: str | None = None,
    ) -> Receipt:
        if len(raw) > MAX_DATAGRAM_BYTES or received_ns < 0:
            raise SpoolError("Invalid datagram receipt metadata")
        return cls(
            receipt_id or uuid.uuid4(),
            incarnation,
            received_ns,
            raw,
            hashlib.sha256(raw).hexdigest(),
            quarantine_reason,
        )

    @property
    def charge(self) -> int:
        return len(self.raw) + 256


@dataclass(frozen=True)
class PendingReceipt:
    sequence: int
    receipt: Receipt = field(repr=False)


class Spool:
    """The owner lock is local SQLite custody, not a network/CNI writer-fencing system."""

    def __init__(
        self,
        root: Path,
        capacity_bytes: int = 1 << 30,
        data_capacity_bytes: int | None = None,
    ) -> None:
        if not root.is_absolute() or capacity_bytes < 131072:
            raise SpoolError("Invalid spool capacity or path")
        self.root, self.capacity_bytes = root, capacity_bytes
        self.reserve_bytes = min(8 << 20, capacity_bytes // 8)
        self.data_capacity_bytes = (
            capacity_bytes - self.reserve_bytes
            if data_capacity_bytes is None
            else data_capacity_bytes
        )
        if not 0 < self.data_capacity_bytes <= capacity_bytes - self.reserve_bytes:
            raise SpoolError("Invalid spool data allowance")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._owner = (root / "owner.lock").open("a+b")
        try:
            fcntl.flock(self._owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._owner.close()
            raise SpoolError("Spool already has an active owner") from None
        self._mutex = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        self.incarnation = str(uuid.uuid4())
        self.checkpoint_errors = 0
        # Process-lifetime counters; quarantined rows each lose exactly one receipt.
        self.quarantined_rows = 0
        self.unreadable_generations = 0
        self._depth = self._pending_bytes = 0
        try:
            self._open()
            self._fsync_dir(self.root / "active")
            self._fsync_dir(self.root)
        except BaseException:
            if self._conn is not None:
                self._conn.close()
            self._owner.close()
            raise

    def _conn_required(self) -> sqlite3.Connection:
        if self._conn is None:
            raise SpoolError("Spool is closed")
        return self._conn

    def _open(self) -> None:
        active = self.root / "active"
        existed = (active / "records.sqlite").exists()
        active.mkdir(mode=0o700, exist_ok=True)
        try:
            self._conn = sqlite3.connect(
                active / "records.sqlite",
                isolation_level=None,
                check_same_thread=False,
                timeout=1,
            )
            db = self._conn
            if existed and db.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise sqlite3.DatabaseError("Spool integrity check failed")
            if not existed:
                db.execute("PRAGMA auto_vacuum=FULL")
            if db.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() != "wal":
                raise sqlite3.DatabaseError("WAL unavailable")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA temp_store=MEMORY")
            db.execute("PRAGMA wal_autocheckpoint=1")
            db.execute("PRAGMA journal_size_limit=65536")
            page_size = db.execute("PRAGMA page_size").fetchone()[0]
            db.execute(
                f"PRAGMA max_page_count={(self.capacity_bytes - self.reserve_bytes) // page_size}"
            )
            db.execute("""
                CREATE TABLE IF NOT EXISTS receipt(
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,receipt_id TEXT NOT NULL,
                    incarnation TEXT NOT NULL,received_ns INTEGER NOT NULL,
                    quarantine_reason TEXT,sha256 TEXT NOT NULL,raw BLOB NOT NULL,
                    charge INTEGER NOT NULL CHECK(charge>0))
            """)
            self._depth, self._pending_bytes = db.execute(
                "SELECT count(*),coalesce(sum(charge),0) FROM receipt"
            ).fetchone()
        except sqlite3.DatabaseError, OSError:
            if self._conn is not None:
                with contextlib.suppress(sqlite3.DatabaseError):
                    self._conn.close()
            self._conn = None
            if not existed:
                raise SpoolError("Spool storage unavailable") from None
            # The file itself is unreadable: keep it intact for offline recovery.
            self._quarantine_active()
            self._open()

    def _quarantine_active(self) -> None:
        destination = self.root / "quarantine"
        destination.mkdir(mode=0o700, exist_ok=True)
        os.rename(self.root / "active", destination / str(uuid.uuid4()))
        self._fsync_dir(destination)
        self._fsync_dir(self.root)
        self.unreadable_generations += 1

    def _quarantine_row(self, sequence: int, raw: Any) -> None:
        destination = self.root / "quarantine"
        destination.mkdir(mode=0o700, exist_ok=True)
        path = destination / f"row-{self.incarnation}-{sequence}.bin"
        with path.open("xb") as stream:
            stream.write(raw if isinstance(raw, bytes) else b"")
            stream.flush()
            os.fsync(stream.fileno())
        self._fsync_dir(destination)
        with self._transaction():
            charge = (
                self._conn_required()
                .execute("DELETE FROM receipt WHERE sequence=? RETURNING charge", (sequence,))
                .fetchone()
            )
        if charge:
            self._depth -= 1
            self._pending_bytes -= charge[0]
        self.quarantined_rows += 1

    @staticmethod
    def _fsync_dir(path: Path) -> None:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    @contextlib.contextmanager
    def _transaction(self) -> Any:
        db = self._conn_required()
        db.execute("BEGIN IMMEDIATE")
        try:
            yield
            db.execute("COMMIT")
        except BaseException:
            with contextlib.suppress(sqlite3.DatabaseError):
                db.execute("ROLLBACK")
            raise

    def _checkpoint(self) -> None:
        try:
            result = self._conn_required().execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        except sqlite3.DatabaseError:
            result = (1,)
        if result[0]:
            self.checkpoint_errors += 1

    def physical_bytes(self) -> int:
        """Active generation only; quarantined files do not count against capacity."""
        try:
            return sum(
                path.stat().st_size for path in (self.root / "active").iterdir() if path.is_file()
            )
        except OSError:
            raise SpoolError("Spool capacity accounting unavailable") from None

    def append(self, receipt: Receipt) -> bool:
        with self._mutex:
            if hashlib.sha256(receipt.raw).hexdigest() != receipt.sha256:
                raise SpoolError("Receipt checksum differs")
            try:
                # Leave bounded transaction headroom; no old row is ever overwritten.
                reservation = 2 * receipt.charge + 65536
                if (
                    self._pending_bytes + receipt.charge > self.data_capacity_bytes
                    or self.physical_bytes() + reservation
                    > self.capacity_bytes - self.reserve_bytes
                ):
                    return False
                with self._transaction():
                    self._conn_required().execute(
                        """
                        INSERT INTO receipt(
                            receipt_id,incarnation,received_ns,quarantine_reason,sha256,raw,charge)
                        VALUES(?,?,?,?,?,?,?)
                    """,
                        (
                            str(receipt.id),
                            receipt.receiver_incarnation,
                            receipt.received_ns,
                            receipt.quarantine_reason,
                            receipt.sha256,
                            receipt.raw,
                            receipt.charge,
                        ),
                    )
                self._depth += 1
                self._pending_bytes += receipt.charge
                self._checkpoint()
                return True
            except sqlite3.DatabaseError, OSError:
                raise SpoolError("Spool commit unavailable; receipt outcome is uncertain") from None

    def batch(self, max_rows: int = 128, max_bytes: int = 1 << 20) -> list[PendingReceipt]:
        """Drain-only read. A row that cannot be decoded is moved aside alone."""
        with self._mutex:
            rows = (
                self._conn_required()
                .execute(
                    "SELECT sequence,receipt_id,incarnation,received_ns,quarantine_reason,"
                    "sha256,raw,charge FROM receipt ORDER BY sequence LIMIT ?",
                    (max_rows,),
                )
                .fetchall()
            )
            result: list[PendingReceipt] = []
            charged = 0
            for seq, rid, incarnation, received, reason, sha, raw, charge in rows:
                if result and charged + charge > max_bytes:
                    break
                try:
                    if not isinstance(raw, bytes) or hashlib.sha256(raw).hexdigest() != sha:
                        raise ValueError("Stored checksum differs")
                    if (
                        not isinstance(incarnation, str)
                        or type(received) is not int
                        or not (reason is None or isinstance(reason, str))
                    ):
                        raise TypeError("Stored metadata differs")
                    value = Receipt(uuid.UUID(rid), incarnation, received, raw, sha, reason)
                except ValueError, TypeError, AttributeError:
                    self._quarantine_row(seq, raw)
                    continue
                result.append(PendingReceipt(seq, value))
                charged += charge
            return result

    def acknowledge(self, rows: list[PendingReceipt]) -> None:
        """Caller may invoke this only after the CNPG transaction commit returned successfully."""
        with self._mutex:
            with self._transaction():
                for row in rows:
                    stored = (
                        self._conn_required()
                        .execute(
                            "DELETE FROM receipt WHERE sequence=? AND receipt_id=? AND sha256=? "
                            "RETURNING charge",
                            (row.sequence, str(row.receipt.id), row.receipt.sha256),
                        )
                        .fetchone()
                    )
                    if stored:
                        self._depth -= 1
                        self._pending_bytes -= stored[0]
            self._checkpoint()

    def oldest_received_ns(self) -> int | None:
        with self._mutex:
            value = self._conn_required().execute("SELECT min(received_ns) FROM receipt").fetchone()
            return value[0] if value else None

    def stats(self) -> dict[str, Any]:
        with self._mutex:
            physical = self.physical_bytes()
            fraction = max(
                self._pending_bytes / self.data_capacity_bytes,
                physical / (self.capacity_bytes - self.reserve_bytes),
            )
            return {
                "depth": self._depth,
                "pending_bytes": self._pending_bytes,
                "physical_bytes": physical,
                "capacity_bytes": self.capacity_bytes,
                "data_capacity_bytes": self.data_capacity_bytes,
                "pressure": "critical"
                if fraction >= 0.85
                else "warning"
                if fraction >= 0.70
                else "normal",
                "quarantined_rows": self.quarantined_rows,
                "unreadable_generations": self.unreadable_generations,
                "checkpoint_errors": self.checkpoint_errors,
            }

    def close(self) -> None:
        with self._mutex:
            if self._conn is not None:
                self._checkpoint()
                self._conn.close()
                self._conn = None
            fcntl.flock(self._owner, fcntl.LOCK_UN)
            self._owner.close()
