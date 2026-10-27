"""ffmpeg helpers: normalize audio and find where people are speaking."""
import re
import subprocess
from pathlib import Path

_SILENCE = re.compile(r"silence_(start|end): (-?[\d.]+)")
PAD = 0.2  # seconds kept around each speech region so word edges are not clipped


def _ffmpeg(*args: str) -> str:
    return subprocess.run(["ffmpeg", "-hide_banner", "-y", *args], capture_output=True, text=True, check=True).stderr


def to_wav(src: Path, dst: Path) -> Path:
    _ffmpeg("-i", str(src), "-ac", "1", "-ar", "16000", str(dst))
    return dst


def duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return float(out)


def speech_regions(wav: Path, total: float) -> list[tuple[float, float]]:
    """Everything between silences of >= 0.5 s at -35 dB."""
    log = _ffmpeg("-i", str(wav), "-af", "silencedetect=n=-35dB:d=0.5", "-f", "null", "-")
    regions, start, silent = [], 0.0, False
    for kind, value in _SILENCE.findall(log):
        t = max(0.0, float(value))
        if kind == "start":
            regions.append((start, t))
        else:
            start = t
        silent = kind == "start"
    if not silent:  # the audio ends while someone is still speaking
        regions.append((start, total))
    return [(a, b) for a, b in regions if b - a >= 0.3]


def plan_segments(regions: list[tuple[float, float]], join_gap: float = 2.0,
                  max_len: float = 60.0) -> list[tuple[float, float]]:
    """Join regions separated by short pauses into turns of at most max_len;
    a region longer than max_len on its own is cut into max_len pieces."""
    segments: list[tuple[float, float]] = []
    for a, b in regions:
        if segments and a - segments[-1][1] < join_gap and b - segments[-1][0] <= max_len:
            segments[-1] = (segments[-1][0], b)
            continue
        while b - a > max_len:
            segments.append((a, a + max_len))
            a += max_len
        segments.append((a, b))
    return segments


def cut(wav: Path, start: float, end: float, total: float, dst: Path) -> Path:
    _ffmpeg("-i", str(wav), "-ss", f"{max(0.0, start - PAD):.3f}", "-to", f"{min(total, end + PAD):.3f}", str(dst))
    return dst
