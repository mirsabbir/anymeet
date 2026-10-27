"""The pipeline with a fake provider: ordering, speakers, failures, output files."""
import json
from pathlib import Path

import pytest

from transcriber import pipeline
from tests.test_audio import make_audio


class FakeTranscriber:
    """Returns scripted answers; an Exception in the script is raised instead."""
    name = "fake"

    def __init__(self, *answers):
        self.answers = list(answers)
        self.glossaries = []

    def transcribe(self, audio: Path, glossary: list[str]) -> str:
        self.glossaries.append(glossary)
        answer = self.answers.pop(0) if self.answers else "text"
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture
def two_tracks(tmp_path):
    mic = make_audio(tmp_path / "mic.wav", [("tone", 4), ("quiet", 6)])           # Me speaks at 0 s
    system = make_audio(tmp_path / "system.wav", [("quiet", 5), ("tone", 4)])     # Others speak at 5 s
    return {mic: "Me", system: "Others"}


def test_segments_from_both_tracks_come_out_in_time_order(two_tracks):
    t = FakeTranscriber("আমি আগে বললাম", "Others later")
    segments, length = pipeline.transcribe_tracks(two_tracks, t, ["API"], workers=1)
    assert [s.speaker for s in segments] == ["Me", "Others"]
    assert segments[0].start < segments[1].start
    assert length >= 9
    assert t.glossaries == [["API"], ["API"]]


def test_provider_error_is_kept_visible_not_dropped(two_tracks):
    t = FakeTranscriber(RuntimeError("429 quota"), "ok")
    segments, _ = pipeline.transcribe_tracks(two_tracks, t, [], workers=1)
    failed = [s for s in segments if s.failed]
    assert len(failed) == 1 and "429 quota" in failed[0].failed
    assert len(segments) == 2  # the failed part is still in the transcript


def test_empty_answer_for_long_speech_is_retried_then_marked(tmp_path):
    wav = make_audio(tmp_path / "mic.wav", [("tone", 5)])
    t = FakeTranscriber("", "")
    segments, _ = pipeline.transcribe_tracks({wav: "Me"}, t, [], workers=1)
    assert len(segments) == 1 and segments[0].failed == "empty transcript"
    assert t.answers == []  # asked twice


def test_empty_answer_for_a_short_noise_is_dropped(tmp_path):
    wav = make_audio(tmp_path / "mic.wav", [("tone", 1), ("quiet", 2)])
    segments, _ = pipeline.transcribe_tracks({wav: "Me"}, FakeTranscriber(""), [], workers=1)
    assert segments == []


def test_write_produces_markdown_and_json(two_tracks, tmp_path):
    t = FakeTranscriber("প্রথম কথা", RuntimeError("boom"))
    segments, length = pipeline.transcribe_tracks(two_tracks, t, [], workers=1)
    failed = pipeline.write(segments, length, "fake", "Test meeting", tmp_path / "transcript")
    assert failed == 1
    md = (tmp_path / "transcript.md").read_text(encoding="utf-8")
    assert "**[00:00:00] Me:** প্রথম কথা" in md
    assert "⚠️ This part could not be transcribed" in md
    data = json.loads((tmp_path / "transcript.json").read_text(encoding="utf-8"))
    assert data["model"] == "fake" and len(data["segments"]) == 2
