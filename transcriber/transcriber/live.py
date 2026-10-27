"""Live (near-real-time) transcription while a meeting is still being recorded.

Contract with the desktop apps: while recording, each audio source is appended to
<meeting>/live/<track>.wav (mic.wav = "Me", system.wav = "Others"; any PCM-16 or
float-32 WAV, read as it grows), and <meeting>/live/done appears once recording
has stopped and the files are closed.

Every couple of seconds the new audio is turned into 16 kHz mono and cut at pauses
the same way as after the meeting (audio.plan_segments). A chunk is sent to the
provider as soon as it is followed by a long enough pause, so text arrives during
the meeting; transcript.md/.json are rewritten after every chunk. When `done`
appears the rest is sent, the final transcript is written and live/ is removed.
"""
import shutil
import struct
import subprocess
import tempfile
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

import numpy as np

from . import audio, pipeline
from .base import Transcriber

RATE = 16_000
FRAME = RATE // 50  # 20 ms
SILENCE_DB = -35.0  # same threshold as audio.speech_regions
MIN_PAUSE = 0.5     # pauses shorter than this stay inside a region
JOIN_GAP = 2.0      # a chunk is finished once this much silence follows it
MAX_LEN = 60.0
POLL = 2.0
STALL_TIMEOUT = 600.0  # no new audio and no `done` for this long: assume the app is gone


class GrowingWav:
    """Reads a WAV that another process is still appending to. The header's size
    fields are ignored (they are only correct once the writer closes the file)."""

    def __init__(self, path: Path):
        self.path = path
        self.offset: int | None = None  # where sample data starts, once the header is readable
        self.channels = self.rate = self.bits = self.tag = 0

    def _read_header(self, f) -> bool:
        head = f.read(12)
        if len(head) < 12 or head[:4] != b"RIFF" or head[8:12] != b"WAVE":
            return False
        while True:
            chunk = f.read(8)
            if len(chunk) < 8:
                return False
            cid, size = chunk[:4], struct.unpack("<I", chunk[4:])[0]
            if cid == b"fmt ":
                fmt = f.read(size)
                if len(fmt) < 16:
                    return False
                self.tag, self.channels, self.rate = struct.unpack("<HHI", fmt[:8])
                self.bits = struct.unpack("<H", fmt[14:16])[0]
                if self.tag == 0xFFFE and len(fmt) >= 26:  # WAVE_FORMAT_EXTENSIBLE: real tag is in the GUID
                    self.tag = struct.unpack("<H", fmt[24:26])[0]
            elif cid == b"data":
                self.offset = f.tell()
                return self.channels > 0 and self.rate > 0
            else:
                f.seek(size + (size & 1), 1)

    def read_new(self) -> np.ndarray:
        """New audio since the last call, as 16 kHz mono float32."""
        if not self.path.exists():
            return np.zeros(0, np.float32)
        with self.path.open("rb") as f:
            if self.offset is None and not self._read_header(f):
                return np.zeros(0, np.float32)
            block = self.channels * self.bits // 8
            f.seek(self.offset)
            data = f.read()
        usable = len(data) - len(data) % block
        if usable == 0:
            return np.zeros(0, np.float32)
        self.offset += usable
        if self.tag == 3 and self.bits == 32:
            samples = np.frombuffer(data[:usable], "<f4")
        elif self.tag == 1 and self.bits == 16:
            samples = np.frombuffer(data[:usable], "<i2").astype(np.float32) / 32768
        else:
            raise SystemExit(f"{self.path.name}: unsupported WAV format (tag {self.tag}, {self.bits} bit)")
        mono = samples.reshape(-1, self.channels).mean(axis=1)
        if self.rate == RATE:
            return mono.astype(np.float32)
        n = int(len(mono) * RATE / self.rate)
        return np.interp(np.arange(n) * self.rate / RATE, np.arange(len(mono)), mono).astype(np.float32)


class Track:
    """One audio source: the audio not yet sent, and where it sits in the meeting."""

    def __init__(self, path: Path, speaker: str):
        self.wav = GrowingWav(path)
        self.speaker = speaker
        self.pending = np.zeros(0, np.float32)
        self.start = 0.0  # meeting time (s) of pending[0]

    @property
    def end(self) -> float:
        return self.start + len(self.pending) / RATE

    def ready_segments(self, finished: bool) -> list[tuple[float, float, np.ndarray]]:
        """Chunks that are complete: followed by JOIN_GAP of silence, MAX_LEN long,
        or the recording is over. Returns (start, end, audio) in meeting time."""
        regions = _speech_regions(self.pending)
        out, consumed = [], 0.0
        for a, b in audio.plan_segments(regions, join_gap=JOIN_GAP, max_len=MAX_LEN):
            if not (finished or b + JOIN_GAP <= len(self.pending) / RATE or b - a >= MAX_LEN - 0.01):
                break
            lo, hi = max(0.0, a - audio.PAD), min(len(self.pending) / RATE, b + audio.PAD)
            out.append((self.start + a, self.start + b, self.pending[int(lo * RATE):int(hi * RATE)]))
            consumed = b
        if finished:
            consumed = len(self.pending) / RATE
        elif not out and not regions and len(self.pending) > RATE * JOIN_GAP:
            consumed = len(self.pending) / RATE - JOIN_GAP  # only silence so far: drop it
        if consumed:
            keep = int(consumed * RATE)
            self.pending, self.start = self.pending[keep:], self.start + keep / RATE
        return out


def _speech_regions(samples: np.ndarray) -> list[tuple[float, float]]:
    """Like audio.speech_regions, on samples in memory: runs of 20 ms frames above
    SILENCE_DB, with pauses shorter than MIN_PAUSE kept inside a run."""
    n = len(samples) // FRAME
    if n == 0:
        return []
    frames = samples[: n * FRAME].reshape(n, FRAME)
    loud = 20 * np.log10(np.sqrt((frames ** 2).mean(axis=1)) + 1e-10) > SILENCE_DB
    regions, start, quiet = [], None, 0
    for i, is_loud in enumerate(loud):
        if is_loud:
            start = i if start is None else start
            quiet = 0
        elif start is not None:
            quiet += 1
            if quiet * FRAME / RATE >= MIN_PAUSE:
                regions.append((start, i - quiet + 1))
                start, quiet = None, 0
    if start is not None:
        regions.append((start, n - quiet))
    return [(a * FRAME / RATE, b * FRAME / RATE) for a, b in regions if (b - a) * FRAME / RATE >= 0.3]


def _flac(samples: np.ndarray, path: Path) -> Path:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "f32le", "-ar", str(RATE),
                    "-ac", "1", "-i", "-", str(path)], input=samples.tobytes(), check=True)
    return path


def run(folder: Path, speakers: dict[str, str], t: Transcriber, glossary: list[str],
        workers: int = 8, poll: float = POLL) -> int:
    """Transcribe <folder>/live/*.wav as they grow. Returns the number of failed chunks."""
    live = folder / "live"
    tracks: dict[str, Track] = {}
    jobs: list[tuple[pipeline.Segment, Future]] = []
    lock = threading.Lock()
    out = folder / "transcript"
    last_audio = time.monotonic()

    def write() -> int:
        with lock:
            done = sorted((s for s, f in jobs if f.done() and (s.text or s.failed)), key=lambda s: s.start)
            length = max((tr.end for tr in tracks.values()), default=0.0)
            return pipeline.write(done, length, t.name, folder.name, out)

    with tempfile.TemporaryDirectory(prefix="transcriber-live-") as tmp, ThreadPoolExecutor(workers) as pool:
        n = 0
        while True:
            finished = (live / "done").exists()
            for path in sorted(live.glob("*.wav")) if live.exists() else []:
                with lock:
                    track = tracks.setdefault(path.stem, Track(path, speakers.get(path.stem, path.stem)))
                new = track.wav.read_new()
                if len(new):
                    track.pending = np.concatenate([track.pending, new])
                    last_audio = time.monotonic()
                for a, b, clip in track.ready_segments(finished):
                    seg = pipeline.Segment(track.speaker, a, b)
                    future = pool.submit(pipeline._transcribe, t, seg, _flac(clip, Path(tmp) / f"{n:05d}.flac"), glossary)
                    with lock:
                        jobs.append((seg, future))
                    future.add_done_callback(lambda _: write())
                    n += 1
                    print(f"[{audio_clock(a)}] {track.speaker}: chunk {n} sent ({b - a:.0f}s)", flush=True)
            if finished or time.monotonic() - last_audio > STALL_TIMEOUT:
                break
            time.sleep(poll)
        for _, future in jobs:
            future.result()
    failed = write()
    shutil.rmtree(live, ignore_errors=True)
    return failed


def audio_clock(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"
