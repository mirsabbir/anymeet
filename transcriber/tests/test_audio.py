"""Audio splitting: real ffmpeg on generated audio, and the pure segment planner."""
import subprocess
from pathlib import Path

from transcriber import audio


def make_audio(path: Path, pattern: list[tuple[str, float]]) -> Path:
    """WAV made of tone ("tone") and silence ("quiet") pieces, 16 kHz mono."""
    inputs, labels = [], []
    for i, (kind, seconds) in enumerate(pattern):
        src = f"sine=frequency=440:sample_rate=16000:duration={seconds}" if kind == "tone" else f"anullsrc=r=16000:cl=mono:d={seconds}"
        inputs += ["-f", "lavfi", "-i", src]
        labels.append(f"[{i}:a]")
    graph = "".join(labels) + f"concat=n={len(pattern)}:v=0:a=1"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inputs,
                    "-filter_complex", graph, "-ac", "1", "-ar", "16000", str(path)], check=True)
    return path


def test_speech_regions_finds_the_tones(tmp_path):
    wav = make_audio(tmp_path / "a.wav", [("tone", 2), ("quiet", 1.5), ("tone", 3), ("quiet", 1)])
    regions = audio.speech_regions(wav, audio.duration(wav))
    assert len(regions) == 2
    (a1, b1), (a2, b2) = regions
    assert a1 < 0.2 and 1.8 < b1 < 2.3
    assert 3.3 < a2 < 3.8 and 6.2 < b2 < 6.7


def test_speech_regions_when_audio_ends_mid_speech(tmp_path):
    wav = make_audio(tmp_path / "b.wav", [("quiet", 1), ("tone", 2)])
    total = audio.duration(wav)
    regions = audio.speech_regions(wav, total)
    assert len(regions) == 1
    assert abs(regions[0][1] - total) < 0.05


def test_plan_segments_joins_short_pauses():
    assert audio.plan_segments([(0, 5), (5.5, 10)]) == [(0, 10)]


def test_plan_segments_keeps_long_pauses_apart():
    assert audio.plan_segments([(0, 5), (8, 10)]) == [(0, 5), (8, 10)]


def test_plan_segments_never_exceeds_max_len():
    segments = audio.plan_segments([(0, 50), (50.5, 70), (71, 200)], max_len=60)
    assert all(b - a <= 60 for a, b in segments)
    # Every second of speech is still covered, in order.
    assert segments[0][0] == 0 and segments[-1][1] == 200
    assert all(x[1] <= y[0] for x, y in zip(segments, segments[1:]))


def test_cut_pads_without_going_past_the_ends(tmp_path):
    wav = make_audio(tmp_path / "c.wav", [("tone", 3)])
    total = audio.duration(wav)
    clip = audio.cut(wav, 0.0, total, total, tmp_path / "clip.flac")
    assert abs(audio.duration(clip) - total) < 0.1
