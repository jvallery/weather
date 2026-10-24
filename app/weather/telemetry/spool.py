# SPDX-License-Identifier: MIT
"""One SQLite WAL/FULL commit per datagram; canonical receipt IDs survive replay."""

import contextlib
import fcntl
import hashlib
import json
import os
import sqlite3
import threading
import time
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
    monotonic_ns: int
    interface: int
    source_ip: str = field(repr=False)
    source_port: int = field(repr=False)
    raw: bytes = field(repr=False)
    sha256: str
    quarantine_reason: str | None = None

    @classmethod
    def create(
        cls,
        raw: bytes,
        incarnation: str,
        received_ns: int,
        monotonic_ns: int,
        interface: int,
        source_ip: str,
        source_port: int,
        *,
        receipt_id: uuid.UUID | None = None,
        quarantine_reason: str | None = None,
    ) -> Receipt:
        if len(raw) > MAX_DATAGRAM_BYTES or min(received_ns, monotonic_ns, interface) < 0:
            raise SpoolError("Invalid datagram receipt metadata")
        return cls(
            receipt_id or uuid.uuid4(),
            incarnation,
            received_ns,
            monotonic_ns,
            interface,
            source_ip,
            source_port,
            raw,
            hashlib.sha256(raw).hexdigest(),
            quarantine_reason,
        )

    @property
    def metadata(self) -> dict[str, Any]:
        return {
            "receiver_incarnation": self.receiver_incarnation,
            "received_ns": str(self.received_ns),
            "monotonic_ns": str(self.monotonic_ns),
            "interface": self.interface,
            "source_ip": self.source_ip,
            "source_port": self.source_port,
            "length": len(self.raw),
            "quarantine_reason": self.quarantine_reason,
        }

    @property
    def metadata_json(self) -> str:
        return json.dumps(self.metadata, sort_keys=True, separators=(",", ":"), allow_nan=False)

    @property
    def charge(self) -> int:
        return len(self.raw) + len(self.metadata_json.encode()) + 256


@dataclass(frozen=True)
class PendingReceipt:
    generation: str
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
        self.volatile_loss_unknown = False
        self.diagnostic_loss_unknown = (root / "quarantine").exists()
        self.incarnation = str(uuid.uuid4())
        self.started_ns = time.time_ns()
        self.checkpoint_errors = 0
        try:
            self._open()
            self._register_start()
        except BaseException:
            if self._conn is not None:
                self._conn.close()
            self._owner.close()
            raise

    def _register_start(self) -> None:
        with self._transaction():
            opens = self._meta("opens")
            self.volatile_loss_unknown = self.volatile_loss_unknown or opens > 0
            self._set_meta("opens", opens + 1)
            self._conn_required().execute(
                "INSERT INTO incarnation(id,started_ns) VALUES(?,?)",
                (self.incarnation, self.started_ns),
            )
            if opens:
                self._loss("restart_volatile_unknown", None, time.time_ns())
            if self.diagnostic_loss_unknown:
                self._loss("spool_unreadable", None, time.time_ns())
        self._checkpoint()
        self._fsync_dir(self.root / "active")
        self._fsync_dir(self.root)

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
            db.executescript("""
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS identity(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS incarnation(
                    id TEXT PRIMARY KEY,started_ns INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS receipt(
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,receipt_id TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,metadata_sha256 TEXT NOT NULL,
                    sha256 TEXT NOT NULL,raw BLOB NOT NULL,charge INTEGER NOT NULL CHECK(charge>0));
                CREATE INDEX IF NOT EXISTS receipt_id_idx ON receipt(receipt_id);
                CREATE TABLE IF NOT EXISTS loss(
                    reason TEXT PRIMARY KEY,first_ns INTEGER NOT NULL,last_ns INTEGER NOT NULL,
                    lost_count INTEGER);
                INSERT OR IGNORE INTO meta VALUES
                    ('pending_bytes',0),('pending_count',0),('opens',0);
            """)
            value = db.execute("SELECT value FROM identity WHERE key='generation'").fetchone()
            if value:
                self.generation = value[0]
            else:
                self.generation = str(uuid.uuid4())
                db.execute("INSERT INTO identity VALUES('generation',?)", (self.generation,))
            # Verify counters against the committed ledger, never an arrival-order reconstruction.
            count, charged = db.execute(
                "SELECT count(*),coalesce(sum(charge),0) FROM receipt"
            ).fetchone()
            if count != self._meta("pending_count") or charged != self._meta("pending_bytes"):
                raise sqlite3.DatabaseError("Spool accounting differs")
        except sqlite3.DatabaseError, OSError:
            if self._conn is not None:
                with contextlib.suppress(sqlite3.DatabaseError):
                    self._conn.close()
            self._conn = None
            if not existed:
                raise SpoolError("Spool storage unavailable") from None
            self._quarantine_active()
            self._open()

    def _quarantine_active(self) -> None:
        destination = self.root / "quarantine"
        destination.mkdir(mode=0o700, exist_ok=True)
        incident = str(uuid.uuid4())
        marker = destination / (incident + ".json")
        with marker.open("x") as stream:
            json.dump(
                {
                    "reason": "spool_unreadable",
                    "lost_count": None,
                    "observed_ns": str(time.time_ns()),
                    "generation": incident,
                },
                stream,
            )
            stream.flush()
            os.fsync(stream.fileno())
        os.rename(self.root / "active", destination / incident)
        self._fsync_dir(destination)
        self._fsync_dir(self.root)
        self.volatile_loss_unknown = True
        self.diagnostic_loss_unknown = True

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

    def _meta(self, key: str) -> int:
        return int(
            self._conn_required()
            .execute("SELECT value FROM meta WHERE key=?", (key,))
            .fetchone()[0]
        )

    def _set_meta(self, key: str, value: int) -> None:
        self._conn_required().execute("UPDATE meta SET value=? WHERE key=?", (value, key))

    def _loss(self, reason: str, count: int | None, when_ns: int) -> None:
        self._conn_required().execute(
            """
            INSERT INTO loss VALUES(?,?,?,?) ON CONFLICT(reason) DO UPDATE SET
            last_ns=excluded.last_ns,lost_count=CASE WHEN loss.lost_count IS NULL
            OR excluded.lost_count IS NULL THEN NULL ELSE loss.lost_count+excluded.lost_count END
        """,
            (reason, when_ns, when_ns, count),
        )

    def _checkpoint(self) -> None:
        self._conn_required().execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def physical_bytes(self) -> int:
        return sum(path.stat().st_size for path in self.root.rglob("*") if path.is_file())

    def append(self, receipt: Receipt) -> bool:
        with self._mutex:
            if hashlib.sha256(receipt.raw).hexdigest() != receipt.sha256:
                raise SpoolError("Receipt checksum differs")
            metadata = receipt.metadata_json
            # Leave diagnostic and bounded transaction headroom; no old row is overwritten.
            reservation = 2 * receipt.charge + 65536
            full = self._meta("pending_bytes") + receipt.charge > self.data_capacity_bytes
            full = (
                full
                or self.physical_bytes() + reservation > self.capacity_bytes - self.reserve_bytes
            )
            try:
                with self._transaction():
                    if full:
                        self._loss("capacity_loss", 1, receipt.received_ns)
                    else:
                        self._conn_required().execute(
                            """
                            INSERT INTO receipt(
                                receipt_id,metadata_json,metadata_sha256,sha256,raw,charge)
                            VALUES(?,?,?,?,?,?)
                        """,
                            (
                                str(receipt.id),
                                metadata,
                                hashlib.sha256(metadata.encode()).hexdigest(),
                                receipt.sha256,
                                receipt.raw,
                                receipt.charge,
                            ),
                        )
                        self._set_meta(
                            "pending_bytes", self._meta("pending_bytes") + receipt.charge
                        )
                        self._set_meta("pending_count", self._meta("pending_count") + 1)
                try:
                    self._checkpoint()
                except sqlite3.DatabaseError:
                    self.checkpoint_errors += 1
                return not full
            except sqlite3.DatabaseError, OSError:
                self.diagnostic_loss_unknown = True
                raise SpoolError("Spool commit unavailable; receipt outcome is uncertain") from None

    def batch(self, max_rows: int = 128, max_bytes: int = 1 << 20) -> list[PendingReceipt]:
        with self._mutex:
            rows = (
                self._conn_required()
                .execute(
                    "SELECT sequence,receipt_id,metadata_json,metadata_sha256,sha256,raw,charge "
                    "FROM receipt ORDER BY sequence LIMIT ?",
                    (max_rows,),
                )
                .fetchall()
            )
            result: list[PendingReceipt] = []
            charged = 0
            for seq, rid, encoded, metadata_sha, sha, raw, charge in rows:
                if result and charged + charge > max_bytes:
                    break
                try:
                    if (
                        hashlib.sha256(raw).hexdigest() != sha
                        or hashlib.sha256(encoded.encode()).hexdigest() != metadata_sha
                    ):
                        raise ValueError("Stored checksum differs")
                    metadata = json.loads(encoded)
                    value = Receipt(
                        uuid.UUID(rid),
                        metadata["receiver_incarnation"],
                        int(metadata["received_ns"]),
                        int(metadata["monotonic_ns"]),
                        metadata["interface"],
                        metadata["source_ip"],
                        metadata["source_port"],
                        raw,
                        sha,
                        metadata["quarantine_reason"],
                    )
                    if metadata["length"] != len(raw):
                        raise ValueError("Stored length differs")
                except ValueError, KeyError, TypeError:
                    self._conn_required().close()
                    self._conn = None
                    self._quarantine_active()
                    self._open()
                    self._register_start()
                    raise SpoolError("Spool record quarantined; loss magnitude unknown") from None
                result.append(PendingReceipt(self.generation, seq, value))
                charged += charge
            return result

    def acknowledge(self, rows: list[PendingReceipt]) -> None:
        """Caller may invoke this only after the CNPG transaction commit returned successfully."""
        with self._mutex, self._transaction():
            for row in rows:
                if row.generation != self.generation:
                    raise SpoolError("Cannot acknowledge a different spool generation")
                stored = (
                    self._conn_required()
                    .execute(
                        "SELECT charge FROM receipt WHERE sequence=? AND receipt_id=? AND sha256=?",
                        (row.sequence, str(row.receipt.id), row.receipt.sha256),
                    )
                    .fetchone()
                )
                if stored:
                    self._conn_required().execute(
                        "DELETE FROM receipt WHERE sequence=?", (row.sequence,)
                    )
                    self._set_meta("pending_count", self._meta("pending_count") - 1)
                    self._set_meta("pending_bytes", self._meta("pending_bytes") - stored[0])
        with self._mutex:
            self._checkpoint()

    def stats(self) -> dict[str, Any]:
        with self._mutex:
            used = self._meta("pending_bytes")
            fraction = used / self.data_capacity_bytes
            losses = (
                self._conn_required()
                .execute("SELECT reason,first_ns,last_ns,lost_count FROM loss ORDER BY reason")
                .fetchall()
            )
            return {
                "depth": self._meta("pending_count"),
                "pending_bytes": used,
                "physical_bytes": self.physical_bytes(),
                "capacity_bytes": self.capacity_bytes,
                "data_capacity_bytes": self.data_capacity_bytes,
                "pressure": "critical"
                if fraction >= 0.85
                else "warning"
                if fraction >= 0.70
                else "normal",
                "volatile_loss_unknown": self.volatile_loss_unknown,
                "diagnostic_loss_unknown": self.diagnostic_loss_unknown,
                "checkpoint_errors": self.checkpoint_errors,
                "losses": [
                    {"reason": r, "first_ns": str(a), "last_ns": str(b), "count": n}
                    for r, a, b, n in losses
                ],
            }

    def close(self) -> None:
        with self._mutex:
            if self._conn is not None:
                with contextlib.suppress(sqlite3.DatabaseError):
                    self._checkpoint()
                self._conn.close()
                self._conn = None
            fcntl.flock(self._owner, fcntl.LOCK_UN)
            self._owner.close()
