"""ffmpeg helpers: convert to 16 kHz mono WAV and split long audio at silences."""
import re
import subprocess
from pathlib import Path

_SILENCE = re.compile(r"silence_(start|end): ([\d.]+)")


def _run(*args: str) -> str:
    return subprocess.run(args, capture_output=True, text=True, check=True).stderr


def to_wav(src: Path, dst: Path) -> Path:
    if not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        _run("ffmpeg", "-y", "-i", str(src), "-ac", "1", "-ar", "16000", str(dst))
    return dst


def duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return float(out)


def _silence_midpoints(wav: Path) -> list[float]:
    log = _run("ffmpeg", "-i", str(wav), "-af", "silencedetect=n=-35dB:d=0.3", "-f", "null", "-")
    marks = [float(v) for _, v in _SILENCE.findall(log)]
    return [(a + b) / 2 for a, b in zip(marks[::2], marks[1::2])]


def split(wav: Path, max_seconds: float, out_dir: Path) -> list[Path]:
    """Cut at the last silence before each max_seconds boundary, so words are
    not chopped in half. Falls back to a hard cut when there is no silence."""
    total = duration(wav)
    if total <= max_seconds:
        return [wav]
    mids, cuts, start = _silence_midpoints(wav), [], 0.0
    while total - start > max_seconds:
        inside = [m for m in mids if start + 1 < m <= start + max_seconds]
        start = max(inside) if inside else start + max_seconds
        cuts.append(start)
    out_dir.mkdir(parents=True, exist_ok=True)
    parts = []
    for i, (a, b) in enumerate(zip([0.0, *cuts], [*cuts, total])):
        part = out_dir / f"{wav.stem}.{max_seconds:g}s.{i:03d}.wav"
        if not part.exists():
            _run("ffmpeg", "-y", "-i", str(wav), "-ss", f"{a:.3f}", "-to", f"{b:.3f}", str(part))
        parts.append(part)
    return parts
