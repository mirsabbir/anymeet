"""Tracks -> speech segments -> provider (in parallel) -> one time-ordered transcript.
With speaker identification on, the "Others" track's segments are first cut where
the speaker changes (speakers.py), so each one has a single speaker.

No audio is dropped silently: a segment with speech that still has no text
after a retry is kept in the transcript as a visible warning.

retry_failed sends only the parts an earlier run could not transcribe, cut from the
same tracks at the same times, and rewrites the transcript as each one comes back.

STOP ends a run early (the apps' Stop button, through cli.watch_stop): parts not
yet sent are left as they were, parts already sent finish."""
import json
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from . import audio, speakers
from .base import QuotaExhausted, Transcriber
from .progress import Progress

MIN_SPEECH_FOR_TEXT = 3.0  # seconds; shorter segments may honestly be a cough
OTHERS = "Others"  # the system audio's label; speaker identification splits it into people
STOP = threading.Event()
STOPPED = "stopped before this part was sent"
TRACK_OF = {"Me": "mic"}  # older transcripts have no track: "Me" came from mic, everyone else from system


@dataclass
class Segment:
    speaker: str
    start: float
    end: float
    text: str = ""
    failed: str = ""
    track: str = ""  # the audio file it came from (mic, system), which renaming the speaker doesn't change


def _transcribe(t: Transcriber, seg: Segment, clip: Path, glossary: list[str]) -> None:
    """Fills seg.text, or seg.failed with why not. A part not sent because the run was
    stopped keeps the failure it already had."""
    if STOP.is_set():
        seg.failed = seg.failed or STOPPED
        return
    try:
        for _ in range(2):  # one retry when a long segment comes back empty
            text = t.transcribe(clip, glossary).strip()
            if text or seg.end - seg.start < MIN_SPEECH_FOR_TEXT:
                seg.text, seg.failed = text, ""
                return
        seg.failed = "empty transcript"
    except QuotaExhausted as e:  # says itself what happened and when to try again
        seg.failed = str(e)
    except Exception as e:  # the provider already retried rate limits and 5xx
        seg.failed = f"{type(e).__name__}: {str(e)[:200]}"


def _run(jobs: list[tuple[Segment, Path]], t: Transcriber, glossary: list[str], workers: int,
         progress: Progress, after: Callable[[], None] = lambda: None) -> None:
    progress.stage("transcribing", total=len(jobs))

    def one(job: tuple[Segment, Path]) -> None:
        seg, clip = job
        _transcribe(t, seg, clip, glossary)
        progress.part_done(failed=bool(seg.failed))
        after()
    with ThreadPoolExecutor(workers) as pool:
        list(pool.map(one, jobs))


def transcribe_tracks(tracks: dict[Path, str], t: Transcriber, glossary: list[str], workers: int = 8,
                      diarize: Callable[[Path], list[speakers.Turn]] | None = None,
                      progress: Progress | None = None) -> tuple[list[Segment], float]:
    """tracks maps each audio file to its speaker label. With `diarize` (speakers.diarize),
    the OTHERS track is split into its speakers. Returns segments in time order and the
    longest track's duration."""
    progress = progress or Progress(None)
    jobs, longest = [], 0.0
    with tempfile.TemporaryDirectory(prefix="transcriber-") as tmp:
        for n, (src, speaker) in enumerate(tracks.items()):
            progress.stage("preparing")
            wav = audio.to_wav(src, Path(tmp) / f"{n}.wav")
            total = audio.duration(wav)
            longest = max(longest, total)
            turns = None
            if diarize and speaker == OTHERS and not STOP.is_set():
                progress.stage("speakers")
                turns = diarize(wav)
                progress.stage("preparing")
            for i, (a, b) in enumerate(audio.plan_segments(audio.speech_regions(wav, total))):
                pieces = speakers.split(a, b, turns, speaker) if turns is not None else [(a, b, speaker)]
                for j, (pa, pb, who) in enumerate(pieces):
                    clip = audio.cut(wav, pa, pb, total, Path(tmp) / f"{n}-{i:05d}-{j}.flac")
                    jobs.append((Segment(who, pa, pb, track=src.stem), clip))
        _run(jobs, t, glossary, workers, progress)
    segments = sorted((seg for seg, _ in jobs), key=lambda s: s.start)
    return [s for s in segments if s.text or s.failed], longest


def retry_failed(out: Path, tracks: dict[str, Path], t: Transcriber, glossary: list[str], title: str,
                 workers: int = 8, progress: Progress | None = None) -> tuple[int, int]:
    """Transcribes again only the failed parts of <out>.json, from the tracks (file stem ->
    audio file) they were heard on, and rewrites <out>.md/.json as each one comes back.
    Speakers stay as they were: telling them apart again would mean the whole recording.
    Returns (parts tried, parts still failed)."""
    progress = progress or Progress(None)
    data = json.loads(Path(f"{out}.json").read_text(encoding="utf-8"))
    segments = [Segment(**s) for s in data["segments"]]
    todo = [s for s in segments if s.failed]
    lock, last = threading.Lock(), [0.0]

    def save(force: bool = False) -> None:
        with lock:  # at most every 2 s while running, so a long retry still shows text arriving
            if force or time.monotonic() - last[0] >= 2:
                write([s for s in segments if s.text or s.failed], data["duration"], data["model"], title, out)
                last[0] = time.monotonic()

    jobs = []
    with tempfile.TemporaryDirectory(prefix="transcriber-retry-") as tmp:
        progress.stage("preparing")
        for stem in sorted({s.track or TRACK_OF.get(s.speaker, "system") for s in todo}):
            src = tracks.get(stem)
            mine = [s for s in todo if (s.track or TRACK_OF.get(s.speaker, "system")) == stem]
            if src is None:
                for s in mine:
                    s.failed = f"its audio file ({stem}.*) is not in the meeting folder"
                continue
            wav = audio.to_wav(src, Path(tmp) / f"{stem}.wav")
            total = audio.duration(wav)
            for k, s in enumerate(mine):
                jobs.append((s, audio.cut(wav, s.start, s.end, total, Path(tmp) / f"{stem}-{k:05d}.flac")))
        if jobs and t.name not in data["model"].split(" + "):
            data["model"] = f"{data['model']} + {t.name}"  # parts now come from two models
        _run(jobs, t, glossary, workers, progress, after=save)
    save(force=True)
    return len(jobs), sum(1 for s in segments if s.failed)


def _clock(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def write(segments: list[Segment], length: float, model: str, title: str, out: Path) -> int:
    """Write <out>.md and <out>.json; return the number of failed segments."""
    failed = sum(1 for s in segments if s.failed)
    lines = [
        f"# {title}", "",
        f"Model: {model} · Length: {_clock(length)} · Segments: {len(segments)}"
        + (f" · ⚠️ Not transcribed: {failed}" if failed else ""), "",
    ]
    for s in segments:
        body = s.text or f"⚠️ This part could not be transcribed ({_clock(s.start)}–{_clock(s.end)}): {s.failed}"
        lines.append(f"**[{_clock(s.start)}] {s.speaker}:** {body}\n")
    Path(f"{out}.md").write_text("\n".join(lines), encoding="utf-8")
    Path(f"{out}.json").write_text(
        json.dumps({"model": model, "duration": length, "segments": [asdict(s) for s in segments]},
                   ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    return failed


def rename_speaker(out: Path, title: str, old: str, new: str) -> int:
    """Renames a speaker in <out>.json and rewrites <out>.md. Renaming to a name that is
    already used merges the two (one person split into two speakers). Returns how many
    segments changed."""
    new = " ".join(new.split())
    if not new:
        raise SystemExit("the new name is empty")
    data = json.loads(Path(f"{out}.json").read_text(encoding="utf-8"))
    segments = [Segment(**s) for s in data["segments"]]
    changed = 0
    for s in segments:
        if s.speaker == old:
            s.speaker, changed = new, changed + 1
    if not changed:
        raise SystemExit(f"no speaker called {old!r} in {out}.json")
    write(segments, data["duration"], data["model"], title, out)
    return changed
