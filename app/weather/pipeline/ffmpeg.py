# SPDX-License-Identifier: MIT
"""The pinned FFmpeg: JPEG decode, intra mezzanine blocks, one-pass renditions, validation.

Every invocation is bounded by a timeout, reads no network input and logs only at error level;
any error output is a failure. Decoding uses bit-exact IDCT and scaling, so a frame decodes to
the same bytes on every node. The mezzanine is intra-only at constant QP: each frame's decoded
pixels depend only on that frame, so block size and restarts never change them.
"""

import os
import shutil
import struct
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import IO

TIMESCALE = 60000
TICKS = 1000
SWS = "lanczos+accurate_rnd+full_chroma_int+bitexact"
DECODE_SWS = "bicubic+accurate_rnd+full_chroma_int+bitexact"
DECODE_SECONDS = 30
BLOCK_SECONDS = 900
RENDITION_SECONDS = 2 * 3600
THREADS = "4"


class FFmpegError(RuntimeError):
    """FFmpeg failed or reported an error; carries a fixed stage name only."""

    def __init__(self, stage: str) -> None:
        super().__init__(f"FFmpeg {stage} failed")
        self.stage = stage


class ValidationError(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(f"Rendition invalid: {reason}")
        self.reason = reason


def binary() -> str:
    found = os.environ.get("WEATHER_FFMPEG") or shutil.which("ffmpeg")
    if not found:
        raise FFmpegError("binary")
    return found


def version() -> str:
    result = subprocess.run(
        [binary(), "-hide_banner", "-version"], capture_output=True, timeout=30, check=False
    )
    first = result.stdout.decode(errors="replace").splitlines()[:1]
    if result.returncode or not first:
        raise FFmpegError("version")
    return " ".join(first[0].split()[:3])


def _base() -> list[str]:
    return [binary(), "-nostdin", "-hide_banner", "-loglevel", "error"]


def decode_jpeg(data: bytes, width: int, height: int) -> bytes | None:
    """RGB24 pixels, or None when FFmpeg cannot decode the frame cleanly (a per-slot reject)."""
    try:
        result = subprocess.run(
            [
                *_base(),
                "-flags",
                "+bitexact",
                "-idct",
                "simple",
                "-err_detect",
                "+crccheck+bitstream+buffer+explode",
                "-f",
                "jpeg_pipe",
                "-i",
                "pipe:0",
                "-frames:v",
                "1",
                "-sws_flags",
                DECODE_SWS,
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "pipe:1",
            ],
            input=data,
            capture_output=True,
            timeout=DECODE_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None
    if result.returncode or result.stderr.strip() or len(result.stdout) != width * height * 3:
        return None
    return result.stdout


class BlockEncoder:
    """Streams RGB24 frames into one intra-only mezzanine block (Matroska)."""

    def __init__(self, path: Path, width: int, height: int) -> None:
        self.path, self.frame_bytes = path, width * height * 3
        self._errors: IO[bytes] = tempfile.TemporaryFile()
        self._process = subprocess.Popen(
            [
                *_base(),
                "-y",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "-s",
                f"{width}x{height}",
                "-framerate",
                "60",
                "-i",
                "pipe:0",
                "-c:v",
                "libx264rgb",
                "-preset",
                "faster",
                "-qp",
                "12",
                "-g",
                "1",
                "-bf",
                "0",
                "-threads",
                THREADS,
                "-f",
                "matroska",
                str(path),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=self._errors,
        )

    def write(self, frame: bytes) -> None:
        if len(frame) != self.frame_bytes:
            raise FFmpegError("block frame")
        assert self._process.stdin is not None
        try:
            self._process.stdin.write(frame)
        except BrokenPipeError:
            raise FFmpegError("block") from None

    def close(self) -> None:
        assert self._process.stdin is not None
        try:
            self._process.stdin.close()
        except BrokenPipeError:
            pass
        try:
            code = self._process.wait(timeout=BLOCK_SECONDS)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait()
            raise FFmpegError("block") from None
        self._errors.seek(0)
        failed = code or self._errors.read(1)
        self._errors.close()
        if failed:
            raise FFmpegError("block")

    def abort(self) -> None:
        self._process.kill()
        self._process.wait()
        self._errors.close()


def concat_list(directory: Path, blocks: Sequence[Path]) -> Path:
    path = directory / "mezzanine.ffconcat"
    lines = ["ffconcat version 1.0"] + [f"file '{block.name}'" for block in blocks]
    path.write_text("\n".join(lines) + "\n")
    return path


@dataclass(frozen=True)
class Frames:
    width: int
    height: int
    timebase: Fraction
    # (pts, duration, sha256) per decoded frame, in stream time base.
    frames: tuple[tuple[int, int, str], ...]

    @property
    def hashes(self) -> tuple[str, ...]:
        return tuple(item[2] for item in self.frames)


def framehash(source: Path, *, concat: bool = False, pix_fmt: str | None = None) -> Frames:
    """Fully decode the first video stream and hash every frame."""
    command = [*_base()]
    if concat:
        command += ["-f", "concat", "-safe", "0"]
    command += ["-i", str(source), "-map", "0:v:0"]
    if pix_fmt:
        command += ["-pix_fmt", pix_fmt]
    command += ["-f", "framehash", "-hash", "sha256", "pipe:1"]
    try:
        result = subprocess.run(command, capture_output=True, timeout=RENDITION_SECONDS)
    except subprocess.TimeoutExpired:
        raise FFmpegError("decode") from None
    if result.returncode or result.stderr.strip():
        raise FFmpegError("decode")
    timebase, width, height = Fraction(0), 0, 0
    frames = []
    for line in result.stdout.decode().splitlines():
        if line.startswith("#tb 0:"):
            timebase = Fraction(line.split(":", 1)[1].strip())
        elif line.startswith("#dimensions 0:"):
            width, height = (int(v) for v in line.split(":", 1)[1].strip().split("x"))
        elif line and not line.startswith("#"):
            fields = [field.strip() for field in line.split(",")]
            if fields[0] != "0":
                continue
            frames.append((int(fields[2]), int(fields[3]), fields[5]))
    return Frames(width, height, timebase, tuple(frames))


@dataclass(frozen=True)
class RenditionSpec:
    role: str
    width: int
    height: int
    crf: int


def encode_renditions(
    concat: Path, frames: int, outputs: Sequence[tuple[RenditionSpec, Path]]
) -> None:
    """One FFmpeg: decode the mezzanine once, split, scale and encode every rendition."""
    labels = [f"r{index}" for index in range(len(outputs))]
    graph = f"[0:v]setpts=N/(60*TB),split={len(outputs)}" + "".join(
        f"[s{label}]" for label in labels
    )
    for label, (spec, _) in zip(labels, outputs, strict=True):
        graph += (
            f";[s{label}]scale={spec.width}:{spec.height}:flags={SWS}"
            ":out_color_matrix=bt709:out_range=tv,format=yuv420p,setsar=1,"
            "setparams=range=tv:color_primaries=bt709:color_trc=bt709:colorspace=bt709"
            f"[{label}]"
        )
    command = [*_base(), "-y", "-f", "concat", "-safe", "0", "-i", str(concat)]
    command += ["-filter_complex", graph]
    for label, (spec, path) in zip(labels, outputs, strict=True):
        command += [
            "-map",
            f"[{label}]",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            str(spec.crf),
            "-profile:v",
            "high",
            "-g",
            "120",
            "-threads",
            THREADS,
            "-frames:v",
            str(frames),
            "-fps_mode",
            "cfr",
            "-r",
            "60",
            "-an",
            "-colorspace",
            "bt709",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            "-color_range",
            "tv",
            "-video_track_timescale",
            str(TIMESCALE),
            "-movie_timescale",
            str(TIMESCALE),
            "-movflags",
            "+faststart+write_colr",
            "-f",
            "mp4",
            str(path),
        ]
    try:
        result = subprocess.run(command, capture_output=True, timeout=RENDITION_SECONDS)
    except subprocess.TimeoutExpired:
        raise FFmpegError("renditions") from None
    if result.returncode or result.stderr.strip():
        raise FFmpegError("renditions")


def extract_frame(path: Path, index: int, width: int, height: int) -> bytes:
    """Decode exactly frame `index` (PTS index/60) of a validated rendition as RGB24."""
    # The first frame at or after floor_us(index/60) is frame `index`: frame index-1 starts
    # 1/60 s earlier, and microsecond truncation moves the seek point back by under 1 us.
    seek = Fraction(index, 60)
    micros = seek.numerator * 1_000_000 // seek.denominator
    command = [
        *_base(),
        "-ss",
        f"{micros // 1_000_000}.{micros % 1_000_000:06d}",
        "-i",
        str(path),
        "-frames:v",
        "1",
        "-sws_flags",
        DECODE_SWS,
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "pipe:1",
    ]
    try:
        result = subprocess.run(command, capture_output=True, timeout=600)
    except subprocess.TimeoutExpired:
        raise FFmpegError("poster") from None
    if result.returncode or result.stderr.strip() or len(result.stdout) != width * height * 3:
        raise FFmpegError("poster")
    return result.stdout


# MP4 structure ------------------------------------------------------------------------------

CONTAINERS = {"moov", "trak", "mdia", "minf", "stbl", "edts"}


@dataclass
class Mp4:
    top: list[str]
    movie_timescale: int = 0
    movie_duration: int = 0
    tracks: int = 0
    handler: str = ""
    media_timescale: int = 0
    media_duration: int = 0
    width: Fraction = Fraction(0)
    height: Fraction = Fraction(0)
    sample_entry: str = ""
    stts: list[tuple[int, int]] | None = None
    edits: list[tuple[int, int, int]] | None = None
    colr: tuple[int, int, int, int] | None = None


def _boxes(data: bytes, start: int, end: int) -> list[tuple[str, int, int]]:
    """(type, payload start, box end) for each box in [start, end)."""
    found = []
    offset = start
    while offset + 8 <= end:
        size, kind = struct.unpack(">I4s", data[offset : offset + 8])
        header = 8
        if size == 1:
            size = struct.unpack(">Q", data[offset + 8 : offset + 16])[0]
            header = 16
        elif size == 0:
            size = end - offset
        if size < header or offset + size > end:
            raise ValidationError("box_structure")
        found.append((kind.decode("latin-1"), offset + header, offset + size))
        offset += size
    if offset != end:
        raise ValidationError("box_structure")
    return found


def _times(data: bytes, at: int) -> tuple[int, int]:
    """(timescale, duration) of an mvhd/mdhd payload."""
    if data[at] == 1:
        return struct.unpack(">IQ", data[at + 20 : at + 32])
    return struct.unpack(">II", data[at + 12 : at + 20])


def inspect_mp4(path: Path) -> Mp4:
    data = _head_and_moov(path)
    top = _boxes(data, 0, len(data))
    info = Mp4(top=[kind for kind, _, _ in top])

    def walk(start: int, end: int) -> None:
        for kind, at, stop in _boxes(data, start, end):
            if kind in CONTAINERS:
                if kind == "trak":
                    info.tracks += 1
                walk(at, stop)
            elif kind == "mvhd":
                info.movie_timescale, info.movie_duration = _times(data, at)
            elif kind == "mdhd":
                info.media_timescale, info.media_duration = _times(data, at)
            elif kind == "hdlr":
                info.handler = data[at + 8 : at + 12].decode("latin-1")
            elif kind == "tkhd":
                offset = at + (88 if data[at] == 1 else 76)
                w, h = struct.unpack(">II", data[offset : offset + 8])
                info.width, info.height = Fraction(w, 65536), Fraction(h, 65536)
            elif kind == "stts":
                (count,) = struct.unpack(">I", data[at + 4 : at + 8])
                info.stts = [
                    struct.unpack(">II", data[at + 8 + 8 * i : at + 16 + 8 * i])
                    for i in range(count)
                ]
            elif kind == "elst":
                version = data[at]
                (count,) = struct.unpack(">I", data[at + 4 : at + 8])
                size = 20 if version == 1 else 12
                fmt = ">QqI" if version == 1 else ">IiI"
                info.edits = [
                    struct.unpack(fmt, data[at + 8 + size * i : at + 8 + size * (i + 1)])
                    for i in range(count)
                ]
            elif kind == "stsd":
                entries = _boxes(data, at + 8, stop)
                if len(entries) != 1:
                    raise ValidationError("sample_entries")
                entry, entry_at, entry_end = entries[0]
                info.sample_entry = entry
                for child, child_at, _ in _boxes(data, entry_at + 78, entry_end):
                    if child == "colr" and data[child_at : child_at + 4] == b"nclx":
                        primaries, transfer, matrix = struct.unpack(
                            ">HHH", data[child_at + 4 : child_at + 10]
                        )
                        info.colr = (primaries, transfer, matrix, data[child_at + 10] >> 7)

    for kind, at, stop in top:
        if kind == "moov":
            walk(at, stop)
    return info


def _head_and_moov(path: Path) -> bytes:
    """Top-level box headers plus the full moov payload; mdat bytes are never loaded."""
    chunks = bytearray()
    with path.open("rb") as handle:
        size_total = os.fstat(handle.fileno()).st_size
        offset = 0
        while offset < size_total:
            handle.seek(offset)
            header = handle.read(16)
            if len(header) < 8:
                raise ValidationError("box_structure")
            size, kind = struct.unpack(">I4s", header[:8])
            length = 8
            if size == 1:
                size, length = struct.unpack(">Q", header[8:16])[0], 16
            elif size == 0:
                size = size_total - offset
            if size < length or offset + size > size_total:
                raise ValidationError("box_structure")
            if kind in (b"moov", b"ftyp"):
                if size > 64 * 1024 * 1024:
                    raise ValidationError("box_structure")
                handle.seek(offset)
                chunks += handle.read(size)
            else:
                # Keep the box's position and type, but replace its payload with nothing.
                chunks += struct.pack(">I4s", 8, kind)
            offset += size
    return bytes(chunks)


@dataclass(frozen=True)
class Expect:
    frames: int
    width: int
    height: int


def validate_rendition(path: Path, expect: Expect) -> Frames:
    """Container timing (both timescales 60000, N samples of 1000 ticks, start at 0), colour
    tags and geometry, then a full decode of every frame at k/60."""
    info = inspect_mp4(path)
    terminal = expect.frames * TICKS
    if not info.top or info.top[0] != "ftyp":
        raise ValidationError("ftyp")
    if "moov" not in info.top or "mdat" not in info.top:
        raise ValidationError("boxes")
    if info.top.index("moov") > info.top.index("mdat"):
        raise ValidationError("faststart")
    if info.tracks != 1 or info.handler != "vide" or info.sample_entry != "avc1":
        raise ValidationError("tracks")
    if info.movie_timescale != TIMESCALE:
        raise ValidationError("movie_timescale")
    if info.media_timescale != TIMESCALE:
        raise ValidationError("track_timescale")
    if (
        not info.stts
        or any(delta != TICKS for _, delta in info.stts)
        or sum(count for count, _ in info.stts) != expect.frames
        or info.media_duration != terminal
    ):
        raise ValidationError("sample_timing")
    if info.movie_duration != terminal:
        raise ValidationError("movie_duration")
    if info.edits is not None and (
        len(info.edits) != 1 or info.edits[0][0] != terminal or info.edits[0][2] != 0x10000
    ):
        raise ValidationError("edit_list")
    if info.width != expect.width or info.height != expect.height:
        raise ValidationError("geometry")
    if info.colr != (1, 1, 1, 0):
        raise ValidationError("colour")
    decoded = framehash(path)
    if (decoded.width, decoded.height) != (expect.width, expect.height):
        raise ValidationError("geometry")
    if len(decoded.frames) != expect.frames:
        raise ValidationError("frame_count")
    for index, (pts, duration, _) in enumerate(decoded.frames):
        if pts * decoded.timebase != Fraction(index, 60) or duration * decoded.timebase != Fraction(
            1, 60
        ):
            raise ValidationError("frame_timing")
    return decoded
