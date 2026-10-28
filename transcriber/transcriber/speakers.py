"""Tells apart the people in the system audio ("Others") on this computer, with
pyannote's community-1 model: who spoke when, as "Speaker 1", "Speaker 2"… in order
of first words. The pipeline then splits each transcript segment where the speaker
changes (split()), so every segment has one speaker; the apps let the user rename them.

Works the same with every transcriber, since it only listens to voices. Needs
`--extra speakers` and the model files (models.SPEAKERS); pyannote's usage telemetry
is switched off, so nothing about the meeting leaves the computer."""
import os
import tempfile
import time
import wave
from pathlib import Path

import numpy as np

from . import device, models

# Set before anything imports pyannote: its usage telemetry stays off, and matplotlib (which
# it imports) keeps its font cache in the temp folder instead of creating ~/.matplotlib.
os.environ["PYANNOTE_METRICS_ENABLED"] = "false"
os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "anymeet-matplotlib"))

Turn = tuple[float, float, str]  # start, end (seconds), speaker
MIN_PIECE = 0.5  # seconds; a shorter turn inside someone else's speech is folded into its neighbour


def available() -> bool:
    return models.downloaded(models.SPEAKERS)


def diarize(wav: Path) -> list[Turn]:
    """Speaker turns of a 16 kHz mono PCM-16 WAV (audio.to_wav), without overlaps."""
    import torch
    from huggingface_hub import snapshot_download
    from pyannote.audio import Pipeline

    started = time.monotonic()
    # The downloaded copy, so nothing is fetched (or even checked) online while transcribing.
    pipeline = Pipeline.from_pretrained(snapshot_download(models.SPEAKERS.repo, local_files_only=True))
    where = device.torch_device()
    pipeline.to(torch.device(where))
    with wave.open(str(wav)) as w:
        rate, pcm = w.getframerate(), np.frombuffer(w.readframes(w.getnframes()), np.int16)
    waveform = torch.from_numpy(pcm.astype(np.float32) / 32768).unsqueeze(0)
    output = pipeline({"waveform": waveform, "sample_rate": rate})
    names: dict[str, str] = {}
    turns = [(seg.start, seg.end, names.setdefault(label, f"Speaker {len(names) + 1}"))
             for seg, _, label in output.exclusive_speaker_diarization.itertracks(yield_label=True)]
    print(f"speakers: {len(names)} in {len(pcm) / rate / 60:.1f} min of system audio, "
          f"{time.monotonic() - started:.0f}s on {where}", flush=True)
    return turns


def split(start: float, end: float, turns: list[Turn], fallback: str) -> list[Turn]:
    """[start, end] cut where the speaker changes: pieces that cover it end to end, one
    speaker each. Without any turn inside, the nearest speaker (or `fallback`) gets it all."""
    inside = [(max(a, start), min(b, end), s) for a, b, s in turns if b > start and a < end]
    if not inside:
        nearest = min(turns, key=lambda t: max(t[0] - end, start - t[1]), default=None)
        return [(start, end, nearest[2] if nearest else fallback)]
    pieces = _merge(inside)
    while len(pieces) > 1:
        short = min(range(len(pieces)), key=lambda i: pieces[i][1] - pieces[i][0])
        a, b, _ = pieces[short]
        if b - a >= MIN_PIECE:
            break
        n = short - 1 if short > 0 else 1  # fold into the previous piece, or the next for the first
        pieces[n] = (min(a, pieces[n][0]), max(b, pieces[n][1]), pieces[n][2])
        del pieces[short]
        pieces = _merge(pieces)
    # Silence between turns goes to the piece before it; the ends stretch to the segment's.
    return [(start if i == 0 else a, end if i == len(pieces) - 1 else pieces[i + 1][0], s)
            for i, (a, _, s) in enumerate(pieces)]


def _merge(pieces: list[Turn]) -> list[Turn]:
    out: list[Turn] = []
    for a, b, s in pieces:
        if out and out[-1][2] == s:
            out[-1] = (out[-1][0], max(out[-1][1], b), s)
        else:
            out.append((a, b, s))
    return out
