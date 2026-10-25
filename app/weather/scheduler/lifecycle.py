# SPDX-License-Identifier: MIT
"""Object lifecycle (#19): reconcile, retention expiry, orphan/tombstone GC and hard purge.

- **Reconcile** HEADs every available asset of one bucket (keyset pages, rate-limited) and
  marks a missing or wrong-size object `unavailable` with an `object_event`. The public views
  already hide unavailable assets, so the API reports the video as `media_unavailable`.
- **Expiry** only tombstones (`media_asset.state = 'expired'`); it never deletes bytes. Videos,
  posters and sidecars expire 365 days after their cycle date (`WEATHER_RETENTION_ENABLED`);
  stills, originals and keograms five years after their local calendar date
  (`WEATHER_HOURLY_ARCHIVE_DELETION_ENABLED`). Both flags default to false: a run then lists
  what would expire and changes nothing. An asset another, unexpired product also publishes
  is never expired. Video expiry never touches stills, events, days or slots.
- **GC** deletes bytes only for objects no restorable catalog state references: keys of
  orphaned upload intents (#18) and expired assets, each past `WEATHER_GC_GRACE_DAYS` (90). A
  key any asset row, pending or committed intent, or orphan still inside grace names is kept.
  It refuses to run unless the PITR window (#6) and the B2 version retention (storage-infra)
  are configured and grace >= max(both) + 7 days. Originals need the hourly-archive flag.
  `WEATHER_GC_ENABLED` defaults to false (a dry run that lists candidates).
- **Hard purge** removes one withdrawn asset's bytes now, overriding grace, only on Jason's
  explicit instruction; it leaves a `purged` audit row with the reason and actor.
"""

import asyncio
import os
import re
from collections import Counter as Tally
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from prometheus_client import Counter, Gauge
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from weather.publish.objects import ObjectInfo

BUCKETS = ("weather-media", "weather-originals")
SAFETY_DAYS = 7
REASON = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
ACTOR = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@-]{0,63}\Z")

RECONCILE_MISSING = Gauge(
    "weather_reconcile_missing_objects",
    "Referenced objects the last reconcile found missing or wrong-size",
    ["bucket"],
)
RECONCILE_MARKED = Counter(
    "weather_reconcile_marked_unavailable_total", "Assets reconcile marked unavailable", ["bucket"]
)
RECONCILE_ABORTS = Counter(
    "weather_reconcile_aborts_total",
    "Reconcile runs stopped by the circuit breaker before marking anything",
    ["bucket", "reason"],
)
GC_CANDIDATES = Gauge(
    "weather_gc_candidates", "Objects past grace that GC may delete", ["bucket", "reason"]
)
GC_DELETIONS = Counter(
    "weather_gc_deletions_total", "Objects GC deleted", ["bucket", "reason"]
)
GC_REFUSALS = Counter("weather_gc_refusals_total", "GC runs refused by the grace guard", ["reason"])
EXPIRY_CANDIDATES = Gauge(
    "weather_expiry_candidates", "Assets past retention, by rule", ["rule"]
)


class Store(Protocol):
    bucket: str

    async def head(self, key: str) -> ObjectInfo | None: ...
    async def delete(self, key: str) -> None: ...
    async def bucket_exists(self) -> bool: ...


def _flag(value: str) -> bool:
    if value.lower() in {"1", "true", "yes"}:
        return True
    if value.lower() in {"0", "false", "no", ""}:
        return False
    raise ValueError("Invalid boolean")


def _days(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    days = int(value)
    if not 0 < days <= 3650:
        raise ValueError("Invalid day count")
    return days


@dataclass(frozen=True)
class LifecycleSettings:
    gc_enabled: bool = False
    grace_days: int = 90
    pitr_window_days: int | None = None
    b2_version_retention_days: int | None = None
    retention_enabled: bool = False
    video_days: int = 365
    hourly_deletion_enabled: bool = False
    hourly_retention_years: int = 5

    def __post_init__(self) -> None:
        if (
            not 1 <= self.grace_days <= 3650
            or not 1 <= self.video_days <= 36500
            or not 1 <= self.hourly_retention_years <= 100
        ):
            raise ValueError("Invalid lifecycle configuration")

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> LifecycleSettings:
        env = os.environ if environ is None else environ
        try:
            return cls(
                gc_enabled=_flag(env.get("WEATHER_GC_ENABLED", "false")),
                grace_days=int(env.get("WEATHER_GC_GRACE_DAYS", "90")),
                pitr_window_days=_days(env.get("WEATHER_RECOVERY_PITR_WINDOW_DAYS")),
                b2_version_retention_days=_days(
                    env.get("WEATHER_RECOVERY_B2_VERSION_RETENTION_DAYS")
                ),
                retention_enabled=_flag(env.get("WEATHER_RETENTION_ENABLED", "false")),
                video_days=int(env.get("WEATHER_RETENTION_VIDEO_DAYS", "365")),
                hourly_deletion_enabled=_flag(
                    env.get("WEATHER_HOURLY_ARCHIVE_DELETION_ENABLED", "false")
                ),
                hourly_retention_years=int(env.get("WEATHER_HOURLY_ARCHIVE_RETENTION_YEARS", "5")),
            )
        except ValueError:
            raise ValueError("Invalid lifecycle configuration") from None


class GcRefused(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def check_grace(settings: LifecycleSettings) -> None:
    """P7: grace must exceed every window in which a restorable catalog state can still
    reference an object, plus a week."""
    reason = None
    if settings.pitr_window_days is None:
        reason = "pitr_window_missing"
    elif settings.b2_version_retention_days is None:
        reason = "b2_version_retention_missing"
    elif settings.grace_days < (
        max(settings.pitr_window_days, settings.b2_version_retention_days) + SAFETY_DAYS
    ):
        reason = "grace_too_short"
    if reason is not None:
        GC_REFUSALS.labels(reason).inc()
        raise GcRefused(reason)


# Reconcile ------------------------------------------------------------------------------------


async def _mark_unavailable(
    connection: AsyncConnection, asset_id: UUID, bucket: str, key: str, reason: str
) -> bool:
    updated = await connection.execute(
        text("""
        UPDATE weather.media_asset SET state='unavailable',state_changed_at=clock_timestamp()
        WHERE id=:id AND state='available'
    """),
        {"id": asset_id},
    )
    if updated.rowcount != 1:
        return False
    await _event(connection, "unavailable", bucket, key, asset_id, reason, "reconcile")
    return True


async def _event(
    connection: AsyncConnection,
    action: str,
    bucket: str,
    key: str,
    asset_id: UUID | None,
    reason: str,
    actor: str,
) -> None:
    await connection.execute(
        text("""
        INSERT INTO weather.object_event (action,bucket,object_key,asset_id,reason,actor)
        VALUES (:action,:bucket,:key,:asset,:reason,:actor)
    """),
        {
            "action": action,
            "bucket": bucket,
            "key": key,
            "asset": asset_id,
            "reason": reason,
            "actor": actor,
        },
    )


class ReconcileAborted(RuntimeError):
    def __init__(self, reason: str, counts: Mapping[str, int]) -> None:
        super().__init__(reason)
        self.reason, self.counts = reason, dict(counts)


async def reconcile_objects(
    engine: AsyncEngine,
    store: Store,
    *,
    rate_per_second: float = 20.0,
    page: int = 500,
    max_missing_ratio: float = 0.05,
) -> dict[str, int]:
    """Idempotent: only `available` rows are checked, and each is marked at most once. A store
    error decides nothing for that object.

    Circuit breaker: the bucket must exist, and every row is HEADed before any is marked. If
    more than `max_missing_ratio` of the checked objects are missing (a wrong endpoint, key or
    bucket, or a store outage that answers 404), nothing is marked and ReconcileAborted is
    raised; an operator who has confirmed a real loss reruns with a higher ratio."""
    if store.bucket not in BUCKETS or not 0 < rate_per_second <= 1000:
        raise ValueError("Invalid reconcile target")
    if not 0 <= max_missing_ratio <= 1:
        raise ValueError("Invalid missing ratio")
    counts: Tally[str] = Tally()
    if not await store.bucket_exists():
        RECONCILE_ABORTS.labels(store.bucket, "bucket_missing").inc()
        raise ReconcileAborted("bucket_missing", counts)
    after: UUID | None = None
    delay = 1 / rate_per_second
    missing: list[tuple[UUID, str, str]] = []
    while True:
        async with engine.connect() as connection:
            rows = (
                await connection.execute(
                    text("""
                SELECT id,object_key,bytes FROM weather.media_asset
                WHERE bucket=:bucket AND state='available'
                  AND (CAST(:after AS uuid) IS NULL OR id > CAST(:after AS uuid))
                ORDER BY id LIMIT :page
            """),
                    {"bucket": store.bucket, "after": after, "page": page},
                )
            ).all()
        if not rows:
            break
        after = rows[-1].id
        for row in rows:
            counts["checked"] += 1
            try:
                info = await store.head(str(row.object_key))
            except Exception:
                counts["errors"] += 1
                await asyncio.sleep(delay)
                continue
            if info is not None and info.size == int(row.bytes):
                counts["present"] += 1
            else:
                counts["missing"] += 1
                reason = "missing" if info is None else "size_mismatch"
                missing.append((row.id, str(row.object_key), reason))
            await asyncio.sleep(delay)
    RECONCILE_MISSING.labels(store.bucket).set(counts["missing"])
    if counts["checked"] and counts["missing"] > max_missing_ratio * counts["checked"]:
        RECONCILE_ABORTS.labels(store.bucket, "too_many_missing").inc()
        raise ReconcileAborted("too_many_missing", counts)
    for asset_id, key, reason in missing:
        async with engine.begin() as connection:
            if await _mark_unavailable(connection, asset_id, store.bucket, key, reason):
                counts["marked_unavailable"] += 1
                RECONCILE_MARKED.labels(store.bucket).inc()
    return {k: counts[k] for k in ("checked", "present", "missing", "marked_unavailable", "errors")}


# Expiry ---------------------------------------------------------------------------------------

# One asset per row with the rule that expires it. An asset expires only when every
# publication that references it is expiring under the rules in force.
EXPIRY = """
WITH clock AS (
  SELECT (clock_timestamp() AT TIME ZONE 'America/Denver')::date AS today
), expiring AS (
  SELECT p.id, CASE WHEN p.product = 'video' THEN 'video' ELSE 'hourly' END AS rule
  FROM weather.publication p, clock
  WHERE (:video AND p.product = 'video'
         AND CAST(left(p.product_key, 10) AS date) < clock.today - CAST(:video_days AS integer))
     OR (:hourly AND p.product IN ('still','keogram')
         AND (p.source_start AT TIME ZONE 'America/Denver')::date
             < clock.today - make_interval(years => CAST(:years AS integer)))
)
SELECT a.id, a.class, a.bucket, a.object_key,
  (SELECT min(e.rule) FROM weather.publication_asset pa JOIN expiring e
     ON e.id = pa.publication_id WHERE pa.asset_id = a.id) AS rule
FROM weather.media_asset a
WHERE a.state <> 'expired'
  AND EXISTS (SELECT 1 FROM weather.publication_asset pa JOIN expiring e
              ON e.id = pa.publication_id WHERE pa.asset_id = a.id)
  AND NOT EXISTS (SELECT 1 FROM weather.publication_asset pa
                  WHERE pa.asset_id = a.id
                    AND pa.publication_id NOT IN (SELECT id FROM expiring))
ORDER BY a.id
"""


@dataclass(frozen=True)
class ExpiryCandidate:
    asset_id: UUID
    asset_class: str
    bucket: str
    object_key: str
    rule: str  # video or hourly


async def expiry_candidates(
    connection: AsyncConnection, settings: LifecycleSettings, *, video: bool, hourly: bool
) -> list[ExpiryCandidate]:
    rows = await connection.execute(
        text(EXPIRY),
        {
            "video": video,
            "hourly": hourly,
            "video_days": settings.video_days,
            "years": settings.hourly_retention_years,
        },
    )
    return [
        ExpiryCandidate(r.id, str(r._mapping["class"]), str(r.bucket), str(r.object_key), r.rule)
        for r in rows
    ]


async def expire(
    engine: AsyncEngine, settings: LifecycleSettings, *, dry_run: bool = False
) -> dict[str, Any]:
    """Lists what both rules would expire; tombstones only under the enabled flags."""
    async with engine.connect() as connection:
        listed = await expiry_candidates(connection, settings, video=True, hourly=True)
    by_rule: Tally[str] = Tally(c.rule for c in listed)
    for rule in ("video", "hourly"):
        EXPIRY_CANDIDATES.labels(rule).set(by_rule[rule])
    result: dict[str, Any] = {
        "candidates": dict(sorted(Tally(f"{c.rule}:{c.asset_class}" for c in listed).items())),
        "expired": 0,
    }
    video, hourly = settings.retention_enabled, settings.hourly_deletion_enabled
    if dry_run or not (video or hourly):
        return result
    async with engine.begin() as connection:
        chosen = await expiry_candidates(connection, settings, video=video, hourly=hourly)
        for candidate in chosen:
            updated = await connection.execute(
                text("""
                UPDATE weather.media_asset SET state='expired',state_changed_at=clock_timestamp()
                WHERE id=:id AND state <> 'expired'
            """),
                {"id": candidate.asset_id},
            )
            if updated.rowcount == 1:
                await _event(
                    connection,
                    "expired",
                    candidate.bucket,
                    candidate.object_key,
                    candidate.asset_id,
                    f"retention_{candidate.rule}",
                    "expiry",
                )
                result["expired"] += 1
    return result


# GC -------------------------------------------------------------------------------------------

KEY_OF = "(o->>'class') || '/' || (o->>'sha256') || '.' || (o->>'extension')"
# A key already collected after it became a candidate is skipped; bytes re-published after a
# GC (the asset revived, or a new orphan) become a candidate again from that later time.
GC_ORPHANS = f"""
WITH grace AS (SELECT clock_timestamp() - make_interval(days => CAST(:grace AS integer)) AS t),
keys AS (
  SELECT o->>'class' AS class, {KEY_OF} AS object_key, max(i.resolved_at) AS orphaned_at
  FROM weather.upload_intent i, grace, jsonb_array_elements(i.manifest->'objects') o
  WHERE i.state = 'orphan' AND i.resolved_at < grace.t
  GROUP BY 1, 2
)
SELECT k.class, k.object_key FROM keys k, grace
WHERE (CASE WHEN k.class = 'original' THEN 'weather-originals' ELSE 'weather-media' END)
      = :bucket
  AND (k.class <> 'original' OR :originals)
  AND NOT EXISTS (SELECT 1 FROM weather.media_asset a WHERE a.object_key = k.object_key)
  AND NOT EXISTS (
    SELECT 1 FROM weather.upload_intent r, jsonb_array_elements(r.manifest->'objects') o
    WHERE {KEY_OF} = k.object_key
      AND (r.state IN ('pending','committed') OR r.resolved_at >= grace.t))
  AND NOT EXISTS (SELECT 1 FROM weather.object_event e
                  WHERE e.object_key = k.object_key
                    AND (e.action = 'purged'
                         OR (e.action = 'gc_deleted' AND e.created_at >= k.orphaned_at)))
  AND (CAST(:only AS text) IS NULL OR k.object_key = CAST(:only AS text))
ORDER BY k.object_key LIMIT :limit
"""
GC_TOMBSTONES = f"""
SELECT a.id, a.class, a.object_key FROM weather.media_asset a
WHERE a.bucket = :bucket AND a.state = 'expired'
  AND a.state_changed_at < clock_timestamp() - make_interval(days => CAST(:grace AS integer))
  AND (a.class <> 'original' OR :originals)
  AND NOT EXISTS (
    SELECT 1 FROM weather.upload_intent r, jsonb_array_elements(r.manifest->'objects') o
    WHERE {KEY_OF} = a.object_key AND r.state = 'pending')
  AND NOT EXISTS (SELECT 1 FROM weather.object_event e
                  WHERE e.object_key = a.object_key
                    AND (e.action = 'purged'
                         OR (e.action = 'gc_deleted' AND e.created_at >= a.state_changed_at)))
  AND (CAST(:only AS text) IS NULL OR a.object_key = CAST(:only AS text))
ORDER BY a.object_key LIMIT :limit
"""
# Phase two re-check, under the key lock, after the audit row exists.
STILL_UNREFERENCED = f"""
SELECT NOT EXISTS (
    SELECT 1 FROM weather.upload_intent r, jsonb_array_elements(r.manifest->'objects') o
    WHERE {KEY_OF} = :key AND r.state = 'pending')
  AND CASE WHEN :tombstone
    THEN EXISTS (SELECT 1 FROM weather.media_asset a WHERE a.object_key = :key
                 AND a.state = 'expired')
    ELSE NOT EXISTS (SELECT 1 FROM weather.media_asset a WHERE a.object_key = :key) END
"""


async def lock_object(connection: AsyncConnection, key: str) -> None:
    # The same lock `publish.catalog.lock_objects` takes for intents and commits.
    await connection.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 29))"),
        {"key": f"object:{key}"},
    )


async def gc(
    engine: AsyncEngine,
    store: Store,
    settings: LifecycleSettings,
    *,
    dry_run: bool = False,
    limit: int = 1000,
) -> dict[str, int]:
    """Refuses before reading anything if the grace guard fails. Each deletion is fenced by the
    object key lock the publisher also takes when it registers an intent and when it commits:

    1. under the lock, re-check the candidate and commit its `gc_deleted` audit row;
    2. under the lock again, delete only if no pending intent and no live row references the
       key, then release. An intent registered meanwhile makes step 2 keep the bytes (the
       audit row then records a decision whose bytes remain: the safe failure), and one
       registered after step 2 sees the key gone and re-uploads."""
    check_grace(settings)
    if store.bucket not in BUCKETS:
        raise ValueError("Unknown bucket")
    params = {
        "bucket": store.bucket,
        "grace": settings.grace_days,
        "originals": settings.hourly_deletion_enabled,
        "limit": limit,
        "only": None,
    }
    async with engine.connect() as connection:
        orphans = (await connection.execute(text(GC_ORPHANS), params)).all()
        tombstones = (await connection.execute(text(GC_TOMBSTONES), params)).all()
    GC_CANDIDATES.labels(store.bucket, "orphan").set(len(orphans))
    GC_CANDIDATES.labels(store.bucket, "tombstone").set(len(tombstones))
    counts = {"orphan_candidates": len(orphans), "tombstone_candidates": len(tombstones)}
    counts["deleted"] = 0
    counts["kept_after_audit"] = 0
    if dry_run or not settings.gc_enabled:
        return counts
    for reason, rows in (("orphan", orphans), ("tombstone", tombstones)):
        tombstone = reason == "tombstone"
        query = GC_TOMBSTONES if tombstone else GC_ORPHANS
        for row in rows:
            key = str(row.object_key)
            async with engine.begin() as connection:
                await lock_object(connection, key)
                if not (await connection.execute(text(query), {**params, "only": key})).all():
                    continue
                await _event(
                    connection,
                    "gc_deleted",
                    store.bucket,
                    key,
                    row.id if tombstone else None,
                    reason,
                    "gc",
                )
            async with engine.begin() as connection:
                await lock_object(connection, key)
                free = (
                    await connection.execute(
                        text(STILL_UNREFERENCED), {"key": key, "tombstone": tombstone}
                    )
                ).scalar_one()
                if not free:
                    counts["kept_after_audit"] += 1
                    continue
                await store.delete(key)
            counts["deleted"] += 1
            GC_DELETIONS.labels(store.bucket, reason).inc()
    return counts


# Hard purge -----------------------------------------------------------------------------------


class PurgeRefused(ValueError):
    pass


async def purge(
    engine: AsyncEngine, store: Store, asset_id: UUID, *, reason: str, actor: str
) -> dict[str, str]:
    """Delete one asset's bytes now (P20 third-party privacy, Jason's explicit instruction).
    A public asset must be withdrawn first, so it already serves 404. Leaves a `purged`
    audit row; the catalog row stays, `unavailable`."""
    if not REASON.fullmatch(reason) or not ACTOR.fullmatch(actor):
        raise PurgeRefused("invalid_reason_or_actor")
    async with engine.begin() as connection:
        row = (
            await connection.execute(
                text("""
            SELECT id,class,bucket,object_key,withdrawn FROM weather.media_asset
            WHERE id=:id FOR UPDATE
        """),
                {"id": asset_id},
            )
        ).one_or_none()
        if row is None:
            raise PurgeRefused("not_found")
        if row.bucket != store.bucket:
            raise PurgeRefused("wrong_bucket")
        if row._mapping["class"] != "original" and not row.withdrawn:
            raise PurgeRefused("not_withdrawn")
        await connection.execute(
            text("""
            UPDATE weather.media_asset SET state='unavailable',state_changed_at=clock_timestamp()
            WHERE id=:id
        """),
            {"id": asset_id},
        )
        await _event(connection, "purged", row.bucket, str(row.object_key), row.id, reason, actor)
    # Audited first; a failed delete raises and the operator reruns the same command.
    await store.delete(str(row.object_key))
    return {"purged": str(asset_id), "bucket": str(row.bucket)}
