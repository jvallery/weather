# SPDX-License-Identifier: MIT
"""Staging cleanup (#19): the four-condition rule, crash idempotence, tmp and render sweeps,
watermarks with a fake statvfs and the GC grace guard. The SQL catalog runs against real
PostgreSQL in test_cleanup_integration.py; here an in-memory catalog stands in for it."""

import asyncio
import contextlib
import itertools
import os
import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from weather.pipeline import synthetic
from weather.scheduler import cleanup as cleanup_module
from weather.scheduler.cleanup import (
    CONDITION_QUERIES,
    ChunkRow,
    Cleanup,
    CleanupSettings,
    Deletion,
    Facts,
    Housekeeping,
    Mode,
    RenderState,
    SegmentSpan,
    chunk_identity,
    decide,
    mezzanine_released,
    preview_passed,
    remove,
    render_deletable,
)
from weather.scheduler.lifecycle import GcRefused, LifecycleSettings, check_grace
from weather.scheduler.pressure import Level, Pressure, Watermarks

CAMERA = "skycam_east"
NS = synthetic.NS
W, H = 16, 8
T0 = datetime(2026, 10, 23, 15, 0, tzinfo=UTC)
LAG = timedelta(seconds=6)
REQUIRED = dict.fromkeys(("keogram", "hourly"), Mode.REQUIRED)
EXPECTED = (2000, 0, W, H)  # cadence_ms, phase_ms, width, height of the corpus


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def row(start: float, end: float, newest: float | None = None) -> ChunkRow:
    return ChunkRow(
        id=uuid.uuid4(),
        camera=CAMERA,
        name=f"sealed/{CAMERA}/x/{start}.tar",
        window_start=at(start),
        window_end=at(end),
        newest_capture_at=at(newest if newest is not None else end),
        frames=30,
        bytes=1,
        manifest_sha256="0" * 64,
    )


# The four-condition table ---------------------------------------------------------------------


def test_four_condition_table_keeps_the_chunk_if_any_one_condition_is_unmet() -> None:
    for mezzanine, keogram, hourly, preview in itertools.product((True, False), repeat=4):
        facts = Facts(mezzanine=mezzanine, keogram=keogram, hourly=hourly, preview=preview)
        unmet = decide(facts, REQUIRED)
        expected = next(
            (
                name
                for name, ok in zip(
                    ("mezzanine", "keogram", "hourly", "preview"),
                    (mezzanine, keogram, hourly, preview),
                    strict=True,
                )
                if not ok
            ),
            None,
        )
        assert unmet == expected
    # Deleted only when all four hold.
    assert decide(Facts(True, True, True, True), REQUIRED) is None


def test_an_unwired_feature_keeps_every_chunk_unless_declared_vacuous() -> None:
    unwired = Facts(mezzanine=True, keogram=None, hourly=None, preview=True)
    assert decide(unwired, REQUIRED) == "keogram"
    assert decide(unwired, {**REQUIRED, "keogram": Mode.VACUOUS}) == "hourly"
    vacuous = dict.fromkeys(REQUIRED, Mode.VACUOUS)
    assert decide(unwired, vacuous) is None
    # The mezzanine condition can never be waived: no mode for it exists.
    assert decide(Facts(False, True, True, True), vacuous) == "mezzanine"
    with pytest.raises(ValueError):
        CleanupSettings(modes={**REQUIRED, "mezzanine": Mode.VACUOUS})
    # #38's hourly query is registered; #37's keogram is not yet. Both default to required,
    # and the registered hourly condition can no longer be waived.
    from weather.scheduler.cleanup import hourly_finalized

    assert CONDITION_QUERIES == {"keogram": None, "hourly": hourly_finalized}
    with pytest.raises(ValueError, match="wired"):
        Cleanup(
            CAMERA,
            Path("/staging"),
            FakeCatalog(at(0)),
            CleanupSettings(modes={**REQUIRED, "hourly": Mode.VACUOUS}),
            lag=LAG,
            expected=EXPECTED,
        )
    defaults = CleanupSettings.from_environment({})
    assert defaults.enabled is False and dict(defaults.modes) == REQUIRED


def test_mezzanine_release_is_a_slot_key_and_needs_the_whole_read_range_covered() -> None:
    chunk = row(10, 11)
    open_segment = [SegmentSpan(at(0), at(600), at(11), False)]
    assert mezzanine_released(chunk, open_segment, LAG)
    behind = [SegmentSpan(at(0), at(600), at(10.99), False)]
    assert not mezzanine_released(chunk, behind, LAG)
    assert not mezzanine_released(chunk, [SegmentSpan(at(0), at(600), None, False)], LAG)
    # No frozen schedule over the chunk (site Secret missing): a later segment may read it.
    assert not mezzanine_released(chunk, [], LAG)
    # A schedule gap inside the read range keeps it too.
    gap = [SegmentSpan(at(0), at(10.5), None, True), SegmentSpan(at(10.6), at(600), at(20), False)]
    assert not mezzanine_released(chunk, gap, LAG)
    # A superseding segment that ends before the lag tail is not enough on its own.
    short = [SegmentSpan(at(0), chunk.window_end, None, True)]
    assert not mezzanine_released(chunk, short, LAG)


def test_a_dusk_spanning_chunk_is_kept_until_both_segments_release_it() -> None:
    dusk = at(30)
    chunk = row(29.5, 30.5)
    day = SegmentSpan(at(-600), dusk, dusk, True)  # complete daytime mezzanine
    night_new = SegmentSpan(dusk, at(700), None, False)
    night_early = SegmentSpan(dusk, at(700), at(30.2), False)
    night_past = SegmentSpan(dusk, at(700), at(31), False)
    assert not mezzanine_released(chunk, [day, night_new], LAG)
    assert not mezzanine_released(chunk, [day, night_early], LAG)
    assert mezzanine_released(chunk, [day, night_past], LAG)
    # Daytime not yet complete: its release point is before the chunk's end.
    day_open = SegmentSpan(at(-600), dusk, at(29.9), False)
    assert not mezzanine_released(chunk, [day_open, night_past], LAG)
    # A chunk ending exactly at dusk still has frames whose source time is past dusk.
    edge = row(29, 30)
    assert not mezzanine_released(edge, [day, night_new], LAG)
    assert mezzanine_released(edge, [day, night_early], LAG)


def test_preview_window_is_1800_seconds_plus_one_refresh_after_the_newest_capture() -> None:
    chunk = row(0, 1, newest=1.1)
    newest = chunk.newest_capture_at
    assert not preview_passed(chunk, newest + timedelta(seconds=1860), 60)
    assert preview_passed(chunk, newest + timedelta(seconds=1860, microseconds=1), 60)


def test_render_directory_release_rule() -> None:
    now = at(24 * 60)
    seg = uuid.uuid4()

    def state(**kw: object) -> RenderState:
        base = RenderState(seg, True, "published", now - timedelta(hours=1), True, True)
        return replace(base, **kw)  # type: ignore[arg-type]

    assert render_deletable(state(), now)
    # Finalized but the renditions are not committed (and read back): keep the mezzanine.
    assert not render_deletable(state(published=False), now)
    assert render_deletable(state(published=False, state="unavailable"), now)
    # Unfinalized: the pipeline may still render it until LATE_RENDER after its end.
    assert not render_deletable(state(finalized=False, state="unavailable"), now)
    # LATE_RENDER plus a one-hour margin for the pipeline pod's clock.
    almost = now - timedelta(hours=12, minutes=59)
    assert not render_deletable(state(finalized=False, state="unavailable", ends_at=almost), now)
    late = now - timedelta(hours=13, seconds=1)
    # A segment never started (no release point) may go once the pipeline dropped it.
    assert render_deletable(state(finalized=False, state="unavailable", ends_at=late), now)
    # Started but unfinalized (a deadline-missed partial mezzanine, complete or not): its
    # blocks may be the only copy of released input. Kept however late, whatever the state.
    for kept in ("unavailable", "published", "partial", "processing"):
        for current in (True, False):
            assert not render_deletable(
                state(finalized=False, state=kept, ends_at=late - timedelta(days=30),
                      started=True, current=current),
                now,
            )
    assert not render_deletable(state(finalized=False, state="processing", ends_at=late), now)
    assert render_deletable(
        state(finalized=False, state="processing", ends_at=late, current=False), now
    )


# An in-memory catalog -------------------------------------------------------------------------


@dataclass
class Stored:
    row: ChunkRow
    state: str = "present"


class FakeCatalog:
    def __init__(self, now: datetime) -> None:
        self.clock = now
        self.rows: dict[str, Stored] = {}
        self.segments: list[SegmentSpan] = []
        self.keogram: bool | None = True
        self.hourly: bool | None = True
        self.deletions: dict[str, Deletion] = {}
        self.unlinked_names: set[str] = set()
        self.render_states: dict[tuple[date, str, int], RenderState] = {}
        self.busy = False

    @contextlib.asynccontextmanager
    async def locked(self, camera: str) -> AsyncIterator[bool]:
        yield not self.busy

    async def now(self) -> datetime:
        return self.clock

    async def known(self, names: Sequence[str]) -> set[str]:
        return {n for n in names if n in self.rows}

    async def present(self, camera: str) -> list[ChunkRow]:
        return sorted(
            (s.row for s in self.rows.values() if s.state == "present"),
            key=lambda r: r.window_start,
        )

    async def register(self, rows: Sequence[ChunkRow]) -> list[ChunkRow]:
        added = []
        for r in rows:
            if r.name not in self.rows:
                stored = replace(r, id=uuid.uuid4())
                self.rows[r.name] = Stored(stored)
                added.append(stored)
        return added

    async def missing(self, ids: Sequence[uuid.UUID]) -> None:
        for s in self.rows.values():
            if s.row.id in ids and s.state == "present":
                s.state = "missing"

    async def facts(
        self,
        camera: str,
        chunks: Sequence[ChunkRow],
        now: datetime,
        lag: timedelta,
        refresh_seconds: int,
    ) -> dict[uuid.UUID, Facts]:
        return {
            c.id: Facts(
                mezzanine_released(c, self.segments, lag),
                self.keogram,
                self.hourly,
                preview_passed(c, now, refresh_seconds),
            )
            for c in chunks
            if c.id is not None
        }

    async def pending(self, camera: str) -> list[Deletion]:
        return [d for d in self.deletions.values() if d.name not in self.unlinked_names]

    async def decide(self, deletion: Deletion) -> Deletion | None:
        if deletion.name in self.deletions:
            return None
        decided = replace(deletion, id=uuid.uuid4())
        self.deletions[deletion.name] = decided
        if deletion.chunk_id is not None:
            stored = self.rows[deletion.name]
            assert stored.state == "present"
            stored.state = "deleted"
        return decided

    async def unlinked(self, deletion: Deletion) -> None:
        self.unlinked_names.add(deletion.name)

    async def renders(
        self, camera: str, keys: Sequence[tuple[date, str, int]]
    ) -> dict[tuple[date, str, int], RenderState]:
        return {k: self.render_states[k] for k in keys if k in self.render_states}

    async def oldest_unfinalized(self, camera: str, now: datetime) -> float:
        return self.unfinalized_age

    unfinalized_age = 0.0


def frames(start: datetime, seconds: range) -> list[synthetic.Frame]:
    return [
        synthetic.Frame(int(start.timestamp() + s) * NS, synthetic.solid(W, H, s % 200))
        for s in seconds
    ]


def corpus(staging: Path) -> list[Path]:
    """Ten one-minute chunks from T0 at 0.5 Hz."""
    return synthetic.write_chunks(
        staging,
        CAMERA,
        frames(T0, range(0, 600, 2)),
        geometry=(W, H),
        cadence_ms=2000,
    )


def make(
    staging: Path, catalog: FakeCatalog, *, enabled: bool = True, **kw: object
) -> Cleanup:
    expected = kw.pop("expected", EXPECTED)
    settings = CleanupSettings(enabled=enabled, **kw)  # type: ignore[arg-type]
    return Cleanup(CAMERA, staging, catalog, settings, lag=LAG, expected=expected)  # type: ignore[arg-type]


def run(coroutine: object) -> dict[str, int]:
    return asyncio.run(coroutine)  # type: ignore[arg-type]


def sealed(staging: Path) -> set[str]:
    return {p.name for p in (staging / "sealed" / CAMERA).rglob("*.tar")}


# The pass -------------------------------------------------------------------------------------


def test_chunk_identity_reads_the_manifest_and_binds_the_window(tmp_path: Path) -> None:
    [first, *_] = corpus(tmp_path)
    name = str(first.relative_to(tmp_path))
    identity = chunk_identity(tmp_path, name, CAMERA)
    assert identity.window_start == T0 and identity.window_end == at(1)
    assert identity.frames == 30 and identity.bytes == first.stat().st_size
    # The last capture (58.15 s) precedes the window end, which bounds it from below.
    assert identity.newest_capture_at == identity.window_end
    with pytest.raises(ValueError):
        chunk_identity(tmp_path, name, "skycam_west")


def test_dry_run_inventories_and_evaluates_but_deletes_nothing(tmp_path: Path) -> None:
    paths = corpus(tmp_path)
    catalog = FakeCatalog(at(24 * 60))
    catalog.segments = [SegmentSpan(at(-60), at(600), None, True)]
    before = sealed(tmp_path)
    report = run(make(tmp_path, catalog, enabled=False).run())
    assert report["inventoried"] == len(paths) and report["eligible"] == len(paths)
    assert "deleted_chunk" not in report and sealed(tmp_path) == before
    assert not catalog.deletions
    # An enabled cleanup still honours an explicit dry run.
    report = run(make(tmp_path, catalog).run(dry_run=True))
    assert sealed(tmp_path) == before and not catalog.deletions


def test_eligible_chunks_are_deleted_with_an_audit_row_and_metadata_kept(tmp_path: Path) -> None:
    paths = corpus(tmp_path)
    catalog = FakeCatalog(at(24 * 60))
    # The segment has released only the first five minutes.
    catalog.segments = [SegmentSpan(at(-60), at(600), at(5), False)]
    report = run(make(tmp_path, catalog).run())
    assert report["deleted_chunk"] == 5 and report["pinned_mezzanine"] == 5
    assert sealed(tmp_path) == {p.name for p in paths[5:]}
    for path in paths[:5]:
        name = str(path.relative_to(tmp_path))
        decision = catalog.deletions[name]
        assert decision.kind == "chunk"
        assert decision.sha256 == catalog.rows[name].row.manifest_sha256
        assert catalog.rows[name].state == "deleted" and name in catalog.unlinked_names
    # A second pass is a no-op.
    again = run(make(tmp_path, catalog).run())
    assert "deleted_chunk" not in again and again["inventoried"] == 0


@pytest.mark.parametrize("unmet", ["keogram", "hourly", "preview"])
def test_each_later_condition_alone_keeps_every_chunk(tmp_path: Path, unmet: str) -> None:
    corpus(tmp_path)
    catalog = FakeCatalog(at(24 * 60) if unmet != "preview" else at(20))
    catalog.segments = [SegmentSpan(at(-60), at(600), None, True)]
    if unmet == "keogram":
        catalog.keogram = False
    if unmet == "hourly":
        catalog.hourly = None  # #38 not wired yet
    before = sealed(tmp_path)
    report = run(make(tmp_path, catalog).run())
    assert report[f"pinned_{unmet}"] == 10 and sealed(tmp_path) == before


def test_files_without_a_catalog_row_are_never_touched(tmp_path: Path) -> None:
    corpus(tmp_path)
    hour = tmp_path / "sealed" / CAMERA / "2026-10-23T15Z"
    (hour / "garbage.tar").write_bytes(b"not a tar")
    (hour / "notes.txt").write_text("operator")
    (tmp_path / "sealed" / CAMERA / "lost+found").mkdir()
    # A well-formed chunk of another camera filed under this one.
    synthetic.write_chunks(
        tmp_path / "other", "skycam_west", frames(T0, range(0, 60, 2)), geometry=(W, H),
        cadence_ms=2000,
    )
    [foreign] = (tmp_path / "other").rglob("*.tar")
    os.rename(foreign, hour / "foreign.tar")
    catalog = FakeCatalog(at(24 * 60))
    catalog.segments = [SegmentSpan(at(-60), at(600), None, True)]
    report = run(make(tmp_path, catalog).run())
    assert report["deleted_chunk"] == 10 and report["unindexed"] == 4
    assert {p.name for p in hour.iterdir()} == {"garbage.tar", "notes.txt", "foreign.tar"}


def test_a_crash_between_decision_and_unlink_reruns_cleanly(tmp_path: Path) -> None:
    paths = corpus(tmp_path)
    catalog = FakeCatalog(at(24 * 60))
    catalog.segments = [SegmentSpan(at(-60), at(600), None, True)]
    calls = 0

    def crashing(staging: Path, deletion: Deletion) -> bool:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("share went away mid-pass")
        return remove(staging, deletion)

    with pytest.raises(OSError):
        run(Cleanup(CAMERA, tmp_path, catalog, CleanupSettings(enabled=True), lag=LAG,
                    expected=EXPECTED, remover=crashing).run())
    # Two unlinked, the third decided but not unlinked.
    assert len(catalog.deletions) == 3 and len(catalog.unlinked_names) == 2
    # An operator (or a hard kill) already removed the decided file: still a no-op.
    pending = next(n for n in catalog.deletions if n not in catalog.unlinked_names)
    (tmp_path / pending).unlink()
    report = run(make(tmp_path, catalog).run())
    assert report["resumed"] == 1 and sealed(tmp_path) == set()
    assert len(catalog.deletions) == len(paths) == len(catalog.unlinked_names)
    # Removing an already-gone target directly is a no-op too.
    assert remove(tmp_path, catalog.deletions[pending])


def test_a_chunk_whose_content_changed_is_never_deleted(tmp_path: Path) -> None:
    paths = corpus(tmp_path)
    catalog = FakeCatalog(at(24 * 60))
    catalog.segments = [SegmentSpan(at(-60), at(600), None, False)]
    run(make(tmp_path, catalog).run())  # inventory only: nothing released yet
    assert sealed(tmp_path) == {p.name for p in paths}
    # Rewrite one chunk with other frames under the same name.
    target = paths[0]
    synthetic.write_chunks(
        tmp_path / "other", CAMERA, frames(T0, range(0, 60, 2))[:5], geometry=(W, H),
        cadence_ms=2000,
    )
    [replacement] = (tmp_path / "other").rglob("*.tar")
    os.replace(replacement, target)
    catalog.segments = [SegmentSpan(at(-60), at(600), None, True)]
    report = run(make(tmp_path, catalog).run())
    assert report["identity_mismatch"] == 1 and target.exists()
    assert report["deleted_chunk"] == len(paths) - 1


def test_a_vanished_chunk_is_marked_missing_and_its_row_kept(tmp_path: Path) -> None:
    paths = corpus(tmp_path)
    catalog = FakeCatalog(at(24 * 60))
    run(make(tmp_path, catalog, enabled=False).run())
    paths[0].unlink()
    report = run(make(tmp_path, catalog, enabled=False).run())
    assert report["missing"] == 1
    assert catalog.rows[str(paths[0].relative_to(tmp_path))].state == "missing"
    # A root that is suddenly empty or unmounted marks nothing missing: only rows whose own
    # hour directory was listed can be.
    import shutil

    shutil.rmtree(tmp_path / "sealed")
    report = run(make(tmp_path, catalog, enabled=False).run())
    assert report["missing"] == 0 and report["unlisted"] == len(paths) - 1
    assert sum(s.state == "present" for s in catalog.rows.values()) == len(paths) - 1


def test_other_incarnations_stale_tmp_files_go_and_the_live_one_stays(tmp_path: Path) -> None:
    now = at(24 * 60)
    tmp = tmp_path / "tmp" / CAMERA
    tmp.mkdir(parents=True)
    old_start = int((now - timedelta(hours=8)).timestamp()) * NS
    stale = tmp / f"{old_start}_{old_start + 60 * NS}__deadpod-abc-3.tar"
    stale.write_bytes(b"x" * 100)
    old = (now - timedelta(hours=7)).timestamp()
    os.utime(stale, (old, old))
    live_start = int((now - timedelta(seconds=30)).timestamp()) * NS
    live = tmp / f"{live_start}_{live_start + 60 * NS}__livepod-def-9.tar"
    live.write_bytes(b"y")
    # An old name but a fresh mtime (a writer still appending) is left alone as well.
    busy = tmp / f"{old_start}_{old_start + 60 * NS}__busypod-ghi-1.tar"
    busy.write_bytes(b"z")
    stray = tmp / "unrelated.tar"
    stray.write_bytes(b"?")
    os.utime(stray, (old, old))
    catalog = FakeCatalog(now)
    report = run(make(tmp_path, catalog, enabled=False).run())
    assert report["tmp_stale"] == 1 and stale.exists()
    report = run(make(tmp_path, catalog).run())
    assert report["deleted_tmp"] == 1 and not stale.exists()
    assert live.exists() and busy.exists() and stray.exists()
    decision = catalog.deletions[f"tmp/{CAMERA}/{stale.name}"]
    assert decision.kind == "tmp" and decision.bytes == 100


def test_render_directories_go_only_after_publish_readback(tmp_path: Path) -> None:
    now = at(24 * 60)
    root = tmp_path / "render" / CAMERA
    names = {
        "published": "2026-10-22_daytime_r1-v1",
        "unpublished": "2026-10-22_overnight_r1-v1",
        "unknown": "2026-10-21_daytime_r1-v1",
        "odd": "scratch",
    }
    for name in names.values():
        (root / name / "blocks").mkdir(parents=True)
        (root / name / "blocks" / "00000.mkv").write_bytes(b"m" * 10)
    catalog = FakeCatalog(now)
    published = RenderState(uuid.uuid4(), True, "published", now - timedelta(hours=3), True, True)
    catalog.render_states = {
        (date(2026, 10, 22), "daytime", 1): published,
        # Finalized as published, but no committed publication with all renditions yet.
        (date(2026, 10, 22), "overnight", 1): replace(published, published=False),
    }
    report = run(make(tmp_path, catalog).run())
    assert report["deleted_render"] == 1
    assert {p.name for p in root.iterdir()} == {names["unpublished"], names["unknown"], "scratch"}
    assert catalog.deletions[f"render/{CAMERA}/{names['published']}"].segment_id == (
        published.segment_id
    )


def test_a_held_lock_skips_the_pass(tmp_path: Path) -> None:
    corpus(tmp_path)
    catalog = FakeCatalog(at(24 * 60))
    catalog.busy = True
    assert run(make(tmp_path, catalog).run()) == {"skipped_locked": 1}
    assert not catalog.rows


# Watermarks -----------------------------------------------------------------------------------


@dataclass
class FakeStat:
    f_blocks: int
    f_bavail: int


class FakeShare:
    def __init__(self) -> None:
        self.used = 0.0
        self.error = False

    def __call__(self, path: Path) -> FakeStat:
        if self.error:
            raise OSError("ESTALE")
        return FakeStat(1000, round(1000 * (1 - self.used)))


def test_watermarks_70_85_90_with_resume_below_75(tmp_path: Path) -> None:
    share = FakeShare()
    pressure = Pressure(CAMERA, tmp_path, statvfs=share)
    trace = []
    for used in (0.5, 0.70, 0.84, 0.85, 0.89, 0.90, 0.80, 0.76, 0.75, 0.74, 0.71, 0.69):
        share.used = used
        trace.append(pressure.update())
    N, A, U, S = Level.NORMAL, Level.ALERT, Level.URGENT, Level.STOP
    assert trace == [N, A, A, U, U, S, S, S, S, A, A, N]
    assert not Pressure(CAMERA, tmp_path, statvfs=share).urgent
    # Below 75% but the failed dependency (cleanup) is still unhealthy: hold.
    share.used = 0.86
    pressure.update()
    share.used = 0.60
    assert pressure.update(dependency_healthy=False) is U
    assert not pressure.optional_allowed()
    assert pressure.update(dependency_healthy=True) is N and pressure.optional_allowed()
    # A failed statvfs never relaxes the level.
    share.used = 0.95
    pressure.update()
    share.error = True
    with pytest.raises(OSError):
        pressure.update()
    assert pressure.level is S
    with pytest.raises(ValueError):
        Watermarks(alert=0.8, resume=0.75)


def test_pressure_never_deletes_pinned_input_and_runs_cleanup_every_loop(
    tmp_path: Path,
) -> None:
    paths = corpus(tmp_path)
    catalog = FakeCatalog(at(24 * 60))
    catalog.segments = [SegmentSpan(at(-60), at(600), at(3), False)]
    share = FakeShare()
    share.used = 0.97
    pressure = Pressure(CAMERA, tmp_path, statvfs=share)
    cleanup = make(tmp_path, catalog, interval_seconds=300)
    housekeeping = Housekeeping(cleanup, pressure)

    async def loops() -> None:
        await housekeeping.tick(0.0)
        # Urgent pressure: a loop 10 s later does not clean again (at most once a minute),
        # one 60 s later does, inside the normal interval.
        catalog.segments = [SegmentSpan(at(-60), at(600), at(4), False)]
        await housekeeping.tick(10.0)
        assert sealed(tmp_path) == {p.name for p in paths[3:]}
        await housekeeping.tick(60.0)

    asyncio.run(loops())
    assert pressure.level is Level.STOP
    # Exactly the released chunks went; every pinned one is still there.
    assert sealed(tmp_path) == {p.name for p in paths[4:]}
    # Normal pressure waits for the interval.
    share.used = 0.10
    catalog.segments = [SegmentSpan(at(-60), at(600), None, True)]
    asyncio.run(housekeeping.tick(20.0))
    assert pressure.level is Level.NORMAL and len(sealed(tmp_path)) == 6
    asyncio.run(housekeeping.tick(360.0))
    assert sealed(tmp_path) == set()


def test_housekeeping_survives_a_failing_share(tmp_path: Path) -> None:
    share = FakeShare()
    share.error = True
    catalog = FakeCatalog(at(0))
    cleanup = make(tmp_path, catalog)

    def broken(*_: object) -> tuple[list[str], int]:
        raise OSError("EIO")

    housekeeping = Housekeeping(cleanup, Pressure(CAMERA, tmp_path, statvfs=share))
    original = cleanup_module.scan_sealed
    cleanup_module.scan_sealed = broken  # type: ignore[assignment]
    try:
        asyncio.run(housekeeping.tick(0.0))
    finally:
        cleanup_module.scan_sealed = original
    assert cleanup.healthy is False


# GC grace guard -------------------------------------------------------------------------------


def test_gc_refuses_without_recovery_windows_or_with_short_grace() -> None:
    for settings, reason in (
        (LifecycleSettings(b2_version_retention_days=40), "pitr_window_missing"),
        (LifecycleSettings(pitr_window_days=30), "b2_version_retention_missing"),
        (
            LifecycleSettings(pitr_window_days=30, b2_version_retention_days=84),
            "grace_too_short",
        ),
        (
            LifecycleSettings(grace_days=36, pitr_window_days=30, b2_version_retention_days=1),
            "grace_too_short",
        ),
    ):
        with pytest.raises(GcRefused) as refused:
            check_grace(settings)
        assert refused.value.reason == reason
    check_grace(LifecycleSettings(pitr_window_days=30, b2_version_retention_days=83))
    check_grace(LifecycleSettings(pitr_window_days=30, b2_version_retention_days=40))
    defaults = LifecycleSettings.from_environment({})
    assert (defaults.gc_enabled, defaults.retention_enabled, defaults.hourly_deletion_enabled) == (
        False,
        False,
        False,
    )
    assert defaults.grace_days == 90 and defaults.pitr_window_days is None
    with pytest.raises(ValueError):
        LifecycleSettings.from_environment({"WEATHER_GC_ENABLED": "maybe"})


def test_rendering_continues_at_the_collector_stop_and_pauses_only_at_97_percent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The share can never fill: the collectors stop payloads at 90 % on their own, and the
    pipeline keeps rendering there (rendering is what releases input) but adds no mezzanine
    block past 97 %, whatever the cleanup conditions. It resumes below 97 %."""
    from weather.pipeline import metrics as pipeline_metrics
    from weather.pipeline import schedule
    from weather.pipeline.intake import Expected
    from weather.pipeline.recipe import Recipe
    from weather.pipeline.render import SegmentJob, SegmentRenderer
    from weather.pipeline.worker import PipelineConfig, Worker

    job = SegmentJob(CAMERA, date(2026, 10, 23), "daytime", 1, T0, at(60), 120)
    row = schedule.SegmentRow(id=uuid.uuid4(), state="processing", job=job)

    async def active(*_: object) -> list[schedule.SegmentRow]:
        return [row]

    advanced: list[object] = []

    def advance(self: SegmentRenderer, now: object, *, limit: int | None = None) -> int:
        advanced.append(now)
        return 0

    monkeypatch.setattr(schedule, "active", active)
    monkeypatch.setattr(SegmentRenderer, "advance", advance)
    share = FakeShare()
    pressure = Pressure(CAMERA, tmp_path, statvfs=share)
    catalog = FakeCatalog(at(24 * 60))  # keogram/hourly unwired: nothing is ever released
    catalog.keogram = catalog.hourly = None
    housekeeping = Housekeeping(make(tmp_path, catalog), pressure)
    config = PipelineConfig(
        camera=CAMERA, staging=tmp_path, expected=Expected(CAMERA, 2000, 0, W, H), recipe=Recipe()
    )
    worker = Worker(config, None, None, None, housekeeping=housekeeping)  # type: ignore[arg-type]
    paused = pipeline_metrics.PRESSURE_PAUSED.labels(CAMERA)._value.get()

    def loop(used: float) -> None:
        share.used = used

        async def once() -> None:
            await housekeeping.tick(0.0)
            await worker.step(at(30))

        asyncio.run(once())

    loop(0.93)
    assert pressure.level is Level.STOP and len(advanced) == 1
    loop(0.98)
    assert len(advanced) == 1 and not worker.backoff
    assert pipeline_metrics.PRESSURE_PAUSED.labels(CAMERA)._value.get() == paused + 1
    loop(0.96)
    assert len(advanced) == 2


def test_a_registered_query_can_no_longer_be_waived(monkeypatch: pytest.MonkeyPatch) -> None:
    async def keogram_committed(*_: object) -> set[uuid.UUID]:
        return set()

    monkeypatch.setitem(CONDITION_QUERIES, "keogram", keogram_committed)
    waived = {**REQUIRED, "keogram": Mode.VACUOUS}
    with pytest.raises(ValueError, match="wired"):
        Cleanup(
            CAMERA,
            Path("/staging"),
            FakeCatalog(at(0)),
            CleanupSettings(modes=waived),
            lag=LAG,
            expected=EXPECTED,
        )
    # Even if a vacuous mode slips through, a wired answer is always evaluated.
    assert decide(Facts(True, False, True, True), waived) == "keogram"
    assert decide(Facts(True, None, True, True), waived) is None


def test_chunks_captured_with_other_settings_are_never_released(tmp_path: Path) -> None:
    """A capture.json cadence or geometry mismatch makes every frame a `settings` reject; the
    slates the renderer writes must not release the input."""
    corpus(tmp_path)
    catalog = FakeCatalog(at(24 * 60))
    catalog.segments = [SegmentSpan(at(-60), at(600), None, True)]
    before = sealed(tmp_path)
    for expected in ((1000, 0, W, H), (2000, 333, W, H), (2000, 0, W * 2, H)):
        report = run(make(tmp_path, catalog, expected=expected).run())
        assert report["pinned_config"] == 10 and sealed(tmp_path) == before
    assert run(make(tmp_path, catalog).run())["deleted_chunk"] == 10


def test_deletion_targets_from_the_database_are_revalidated(tmp_path: Path) -> None:
    [chunk, *_] = corpus(tmp_path)
    name = str(chunk.relative_to(tmp_path))
    from weather.scheduler.cleanup import UnsafeTarget

    bad = [
        Deletion(kind="render", camera=CAMERA, name=f"sealed/{CAMERA}", bytes=0, reason="x"),
        Deletion(kind="tmp", camera=CAMERA, name=name, bytes=0, reason="x"),
        Deletion(kind="render", camera=CAMERA, name="render/skycam_west/2026-10-22_daytime_r1-v1",
                 bytes=0, reason="x"),
        Deletion(kind="chunk", camera=CAMERA, name="sealed/skycam_east/../../etc/x.tar",
                 bytes=0, reason="x", sha256="0" * 64),
    ]
    for deletion in bad:
        with pytest.raises(UnsafeTarget):
            remove(tmp_path, deletion)
    # A symlinked camera render directory pointing at sealed input is refused too.
    (tmp_path / "render").mkdir()
    os.symlink(tmp_path / "sealed" / CAMERA, tmp_path / "render" / CAMERA)
    hour = chunk.parent.name
    (tmp_path / "sealed" / CAMERA / "2026-10-22_daytime_r1-v1").mkdir()
    linked = Deletion(
        kind="render", camera=CAMERA, name=f"render/{CAMERA}/2026-10-22_daytime_r1-v1",
        bytes=0, reason="x", segment_id=uuid.uuid4(),
    )
    with pytest.raises(UnsafeTarget):
        remove(tmp_path, linked)
    assert (tmp_path / "sealed" / CAMERA / hour).is_dir() and chunk.exists()
    # In a pass, an unsafe pending row is refused and counted, and the pass carries on.
    catalog = FakeCatalog(at(24 * 60))
    catalog.deletions[linked.name] = replace(linked, id=uuid.uuid4())
    report = run(make(tmp_path, catalog).run())
    assert report["unsafe_target"] == 1 and chunk.exists()


def test_renderer_refuses_to_resume_below_the_durable_release_point(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A restart that cannot see its durable blocks (lost, or a failed listing) must not
    resume at frame 0 over input cleanup may already have deleted."""
    from weather.pipeline import metrics as pipeline_metrics
    from weather.pipeline import schedule
    from weather.pipeline.intake import Expected
    from weather.pipeline.recipe import Recipe
    from weather.pipeline.render import SegmentJob, SegmentRenderer
    from weather.pipeline.worker import PipelineConfig, ReleaseAhead, Worker

    job = SegmentJob(CAMERA, date(2026, 10, 23), "daytime", 1, T0, at(60), 120)
    config = PipelineConfig(
        camera=CAMERA, staging=tmp_path, expected=Expected(CAMERA, 2000, 0, W, H), recipe=Recipe()
    )
    worker = Worker(config, None, None, None)  # type: ignore[arg-type]
    incidents = pipeline_metrics.RELEASE_INCIDENTS.labels(CAMERA)._value.get()
    ahead = schedule.SegmentRow(
        id=uuid.uuid4(), state="processing", job=job, mezzanine_through=at(10),
        mezzanine_recipe=Recipe().id,
    )
    with pytest.raises(ReleaseAhead):
        worker.renderer(ahead)
    assert ahead.id not in worker.renderers
    assert pipeline_metrics.RELEASE_INCIDENTS.labels(CAMERA)._value.get() == incidents + 1
    # Another recipe's release point is refused as well.
    other = replace(ahead, id=uuid.uuid4(), mezzanine_recipe="t360")
    with pytest.raises(ReleaseAhead):
        worker.renderer(other)
    # No release point yet: renders normally.
    fresh = replace(ahead, id=uuid.uuid4(), mezzanine_through=None, mezzanine_recipe=None)
    assert worker.renderer(fresh).rendered == 0
    # A failing listing raises instead of reading as "no blocks".
    real = os.scandir

    def failing(path: object) -> object:
        if str(path).endswith("blocks"):
            raise OSError("ESTALE")
        return real(path)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "scandir", failing)
    with pytest.raises(OSError):
        SegmentRenderer(
            job, staging=tmp_path, expected=config.expected, recipe=Recipe(),
            index=worker.index,
        )


def test_empty_hour_directories_go_only_when_old_by_name_and_mtime(tmp_path: Path) -> None:
    from weather.scheduler.cleanup import remove_empty_hours

    root = tmp_path / "sealed" / CAMERA
    old, fresh = root / "2026-10-20T03Z", root / "2026-10-20T04Z"
    old.mkdir(parents=True)
    fresh.mkdir()
    stamp = datetime(2026, 10, 20, 6, tzinfo=UTC).timestamp()
    os.utime(old, (stamp, stamp))
    remove_empty_hours(tmp_path, CAMERA, datetime(2026, 10, 21, tzinfo=UTC))
    assert not old.exists() and fresh.exists()


def test_reconcile_refuses_a_missing_bucket_before_reading_the_catalog() -> None:
    from weather.scheduler.lifecycle import ReconcileAborted, reconcile_objects

    class Gone:
        bucket = "weather-media"

        async def bucket_exists(self) -> bool:
            return False

    with pytest.raises(ReconcileAborted) as aborted:
        asyncio.run(reconcile_objects(None, Gone()))  # type: ignore[arg-type]
    assert aborted.value.reason == "bucket_missing"


def test_hour_slots_cover_every_hour_a_chunks_frames_can_be_selected_for() -> None:
    from weather.scheduler.cleanup import hour_slots

    hour = datetime(2026, 10, 23, 15, tzinfo=UTC)
    inside = replace(row(0, 1), window_start=hour + timedelta(minutes=20),
                     window_end=hour + timedelta(minutes=21))
    assert hour_slots(inside, LAG) == [hour]
    # Slots just before the hour can carry frames captured after it: both hours count.
    edge = replace(row(0, 1), window_start=hour + timedelta(minutes=59),
                   window_end=hour + timedelta(hours=1))
    assert hour_slots(edge, LAG) == [hour, hour + timedelta(hours=1)]
    assert hour_slots(edge, timedelta(0)) == [hour]


def test_a_finalized_segment_reads_nothing_more_and_releases_its_chunks() -> None:
    """`weather pipeline abandon` (or any finalized result) ends a segment's reads, so its
    partial release point no longer pins input."""
    chunk = row(10, 11)
    partial = SegmentSpan(at(0), at(600), at(5), False)
    assert not mezzanine_released(chunk, [partial], LAG)
    assert mezzanine_released(chunk, [replace(partial, finalized=True)], LAG)


def test_stuck_unfinalized_segments_are_visible(tmp_path: Path) -> None:
    from weather.scheduler.cleanup import OLDEST_UNFINALIZED, UNFINALIZED_BYTES

    root = tmp_path / "render" / CAMERA / "2026-10-22_daytime_r1-v1" / "blocks"
    root.mkdir(parents=True)
    (root / "00000.mkv").write_bytes(b"m" * 100)
    catalog = FakeCatalog(at(24 * 60))
    catalog.unfinalized_age = 7200.0
    catalog.render_states = {
        (date(2026, 10, 22), "daytime", 1): RenderState(
            uuid.uuid4(), False, "unavailable", at(0), False, False, started=True
        )
    }
    report = run(make(tmp_path, catalog).run())
    assert "render_eligible" not in report and root.exists()
    assert UNFINALIZED_BYTES.labels(CAMERA)._value.get() == 100
    assert OLDEST_UNFINALIZED.labels(CAMERA)._value.get() == 7200.0


def test_rendering_yields_after_its_budget_and_backoff_rows_leave_the_prune_floor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from weather.pipeline import schedule
    from weather.pipeline import worker as worker_module
    from weather.pipeline.intake import Expected
    from weather.pipeline.recipe import Recipe
    from weather.pipeline.render import SegmentJob, SegmentRenderer
    from weather.pipeline.worker import PipelineConfig, Worker

    stuck = schedule.SegmentRow(
        id=uuid.uuid4(),
        state="unavailable",
        job=SegmentJob(CAMERA, date(2026, 10, 20), "daytime", 1, at(-3000), at(-2400), 120),
    )
    live = schedule.SegmentRow(
        id=uuid.uuid4(),
        state="processing",
        job=SegmentJob(CAMERA, date(2026, 10, 23), "daytime", 1, T0, at(60), 120),
    )

    async def active(*_: object) -> list[schedule.SegmentRow]:
        return [stuck, live]

    blocks: list[object] = []

    def advance(self: SegmentRenderer, now: object, *, limit: int | None = None) -> int:
        assert limit == 1
        blocks.append(now)
        return 1

    floors: list[object] = []
    monkeypatch.setattr(schedule, "active", active)
    monkeypatch.setattr(SegmentRenderer, "advance", advance)
    monkeypatch.setattr(SegmentRenderer, "released_through", lambda self: None)
    monkeypatch.setattr(worker_module, "RENDER_BUDGET_SECONDS", 0)
    config = PipelineConfig(
        camera=CAMERA, staging=tmp_path, expected=Expected(CAMERA, 2000, 0, W, H), recipe=Recipe()
    )
    worker = Worker(config, None, None, None)  # type: ignore[arg-type]
    monkeypatch.setattr(worker.index, "prune", floors.append)
    now = at(30)
    worker.backoff[stuck.id] = (3, now + timedelta(minutes=10))
    asyncio.run(worker.step(now))
    # One block, then the budget ends the segment's turn and the loop restarts at once.
    assert len(blocks) == 1 and worker.catching_up
    # The stuck segment in backoff does not hold the index back to its start.
    from weather.timemodel._instant import epoch_seconds

    assert floors == [epoch_seconds(live.job.starts_at) - 3600]


def test_chunks_parsed_again_after_a_prune_are_marked_readded(tmp_path: Path) -> None:
    """A segment back from backoff re-reads chunks the index pruned; they are not late."""
    from fractions import Fraction

    from weather.pipeline.intake import ChunkIndex, Expected

    corpus(tmp_path)
    index = ChunkIndex(tmp_path, Expected(CAMERA, 2000, 0, W, H))
    start, end = Fraction(int(T0.timestamp())), Fraction(int(at(10).timestamp()))
    first = index.refresh(start, end)
    assert first and not any(c.readded for c in first)
    index.prune(end + 1)
    assert not index.chunks
    again = index.refresh(start, end)
    assert len(again) == len(first) and all(c.readded for c in again)
    assert not index.forgotten
