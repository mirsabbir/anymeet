"""Tracks -> speech segments -> provider (in parallel) -> one time-ordered transcript.
With speaker identification on, the "Others" track's segments are first cut where
the speaker changes (speakers.py), so each one has a single speaker.

No audio is dropped silently: a segment with speech that still has no text
after a retry is kept in the transcript as a visible warning."""
import json
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from . import audio, speakers
from .base import Transcriber

MIN_SPEECH_FOR_TEXT = 3.0  # seconds; shorter segments may honestly be a cough
OTHERS = "Others"  # the system audio's label; speaker identification splits it into people


@dataclass
class Segment:
    speaker: str
    start: float
    end: float
    text: str = ""
    failed: str = ""
    track: str = ""  # the audio file it came from (mic, system), which renaming the speaker doesn't change


def _transcribe(t: Transcriber, seg: Segment, clip: Path, glossary: list[str]) -> None:
    try:
        for _ in range(2):  # one retry when a long segment comes back empty
            seg.text = t.transcribe(clip, glossary).strip()
            if seg.text or seg.end - seg.start < MIN_SPEECH_FOR_TEXT:
                return
        seg.failed = "empty transcript"
    except Exception as e:  # the provider already retried rate limits and 5xx
        seg.failed = f"{type(e).__name__}: {str(e)[:200]}"


def transcribe_tracks(tracks: dict[Path, str], t: Transcriber, glossary: list[str], workers: int = 8,
                      diarize: Callable[[Path], list[speakers.Turn]] | None = None) -> tuple[list[Segment], float]:
    """tracks maps each audio file to its speaker label. With `diarize` (speakers.diarize),
    the OTHERS track is split into its speakers. Returns segments in time order and the
    longest track's duration."""
    jobs, longest = [], 0.0
    with tempfile.TemporaryDirectory(prefix="transcriber-") as tmp:
        for n, (src, speaker) in enumerate(tracks.items()):
            wav = audio.to_wav(src, Path(tmp) / f"{n}.wav")
            total = audio.duration(wav)
            longest = max(longest, total)
            turns = diarize(wav) if diarize and speaker == OTHERS else None
            for i, (a, b) in enumerate(audio.plan_segments(audio.speech_regions(wav, total))):
                pieces = speakers.split(a, b, turns, speaker) if turns is not None else [(a, b, speaker)]
                for j, (pa, pb, who) in enumerate(pieces):
                    clip = audio.cut(wav, pa, pb, total, Path(tmp) / f"{n}-{i:05d}-{j}.flac")
                    jobs.append((Segment(who, pa, pb, track=src.stem), clip))
        with ThreadPoolExecutor(workers) as pool:
            list(pool.map(lambda job: _transcribe(t, job[0], job[1], glossary), jobs))
    segments = sorted((seg for seg, _ in jobs), key=lambda s: s.start)
    return [s for s in segments if s.text or s.failed], longest


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
