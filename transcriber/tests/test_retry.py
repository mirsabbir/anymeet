"""Try again: only the failed parts are sent, a daily limit stops the run, Stop works,
and the apps can follow progress in progress.json."""
import json
import sys
import time

import pytest

from transcriber import base, cli, pipeline
from transcriber.base import QuotaExhausted
from transcriber.progress import Progress
from tests.test_audio import make_audio
from tests.test_pipeline import FakeTranscriber


@pytest.fixture(autouse=True)
def fresh_stop():
    pipeline.STOP.clear()
    yield
    pipeline.STOP.clear()


@pytest.fixture
def meeting(tmp_path):
    """A meeting folder whose transcript has one good part and two failed ones."""
    make_audio(tmp_path / "mic.wav", [("tone", 4), ("quiet", 6), ("tone", 4)])  # Me at 0 s and 10 s
    make_audio(tmp_path / "system.wav", [("quiet", 5), ("tone", 4)])             # Others at 5 s
    segments = [
        pipeline.Segment("Me", 0.0, 4.0, text="আগের কথা", track="mic"),
        pipeline.Segment("Rahim", 5.0, 9.0, failed="ClientError: 429 RESOURCE_EXHAUSTED", track="system"),
        pipeline.Segment("Me", 10.0, 14.0, failed="ClientError: 429 RESOURCE_EXHAUSTED"),  # older transcript: no track
    ]
    pipeline.write(segments, 14.0, "gemini-3.5-transcribe", tmp_path.name, tmp_path / "transcript")
    return tmp_path


def load(folder):
    return json.loads((folder / "transcript.json").read_text(encoding="utf-8"))


def test_only_the_failed_parts_are_sent_again(meeting):
    t = FakeTranscriber("আমি পরে বললাম", "Others said this")  # sent track by track: mic, then system
    tried, failed = pipeline.retry_failed(meeting / "transcript", {"mic": meeting / "mic.wav", "system": meeting / "system.wav"},
                                          t, ["API"], meeting.name, workers=1)
    assert (tried, failed) == (2, 0)
    assert len(t.glossaries) == 2  # the good part was not sent again
    data = load(meeting)
    assert [(s["speaker"], s["text"], s["failed"]) for s in data["segments"]] == [
        ("Me", "আগের কথা", ""), ("Rahim", "Others said this", ""), ("Me", "আমি পরে বললাম", "")]
    assert data["model"] == "gemini-3.5-transcribe + fake"  # the parts now come from two models
    assert "⚠️" not in (meeting / "transcript.md").read_text(encoding="utf-8")


def test_a_daily_limit_leaves_the_parts_failed_with_the_reason(meeting):
    t = FakeTranscriber(QuotaExhausted("Gemini's daily limit is used up; it resets in 3 h 00 min."), "ok")
    pipeline.retry_failed(meeting / "transcript", {"mic": meeting / "mic.wav", "system": meeting / "system.wav"},
                          t, [], meeting.name, workers=1)
    reasons = [s["failed"] for s in load(meeting)["segments"] if s["failed"]]
    assert reasons == ["Gemini's daily limit is used up; it resets in 3 h 00 min."]  # in full, not cut at 200 characters


def test_a_missing_audio_file_is_said_not_crashed_on(meeting):
    pipeline.retry_failed(meeting / "transcript", {"mic": meeting / "mic.wav"}, FakeTranscriber("x"), [], meeting.name, workers=1)
    others = [s for s in load(meeting)["segments"] if s["speaker"] == "Rahim"][0]
    assert others["failed"] == "its audio file (system.*) is not in the meeting folder"


def test_stop_keeps_unsent_parts_as_they_were(meeting):
    pipeline.STOP.set()
    t = FakeTranscriber("never")
    tried, failed = pipeline.retry_failed(meeting / "transcript", {"mic": meeting / "mic.wav", "system": meeting / "system.wav"},
                                          t, [], meeting.name, workers=1)
    assert (tried, failed) == (2, 2) and t.glossaries == []
    assert all("429" in s["failed"] for s in load(meeting)["segments"] if s["failed"])


def test_stop_marks_new_parts_as_not_sent(tmp_path):
    wav = make_audio(tmp_path / "mic.wav", [("tone", 4)])
    pipeline.STOP.set()
    segments, _ = pipeline.transcribe_tracks({wav: "Me"}, FakeTranscriber("x"), [], workers=1)
    assert [s.failed for s in segments] == [pipeline.STOPPED]


def test_progress_file_follows_the_run_and_goes_away(tmp_path):
    p = Progress(tmp_path / "progress.json", waiting=lambda: 12.4, every=60)
    p.stage("transcribing", total=3)
    p.part_done(failed=False)
    p.part_done(failed=True)
    data = json.loads((tmp_path / "progress.json").read_text())
    assert {k: data[k] for k in ("stage", "done", "total", "failed", "waitSec")} == \
        {"stage": "transcribing", "done": 2, "total": 3, "failed": 1, "waitSec": 12}
    p.close()
    assert not (tmp_path / "progress.json").exists()


def test_stop_file_stops_the_run(tmp_path):
    (tmp_path / cli.STOP_FILE).touch()  # left over from an earlier run: ignored
    ended = cli.watch_stop(tmp_path, every=0.05)
    time.sleep(0.2)
    assert not pipeline.STOP.is_set()
    (tmp_path / cli.STOP_FILE).touch()
    deadline = time.monotonic() + 3
    while not pipeline.STOP.is_set() and time.monotonic() < deadline:
        time.sleep(0.05)
    ended.set()
    assert pipeline.STOP.is_set()


def run_cli(monkeypatch, folder, *args, answers=()):
    fake = FakeTranscriber(*answers)
    monkeypatch.setitem(base.TRANSCRIBERS, "fake", lambda: fake)
    monkeypatch.setattr(sys, "argv", ["transcribe", str(folder), "--transcriber", "fake", "--glossary", str(folder / "none.txt"), *args])
    with pytest.raises(SystemExit) as done:
        cli.main()
        raise SystemExit(0)
    return done.value.code, fake


def test_cli_only_failed_retries_and_cleans_up(monkeypatch, meeting, capsys):
    code, fake = run_cli(monkeypatch, meeting, "--only-failed", answers=("a", "b"))
    assert code == 0 and len(fake.glossaries) == 2
    assert not (meeting / "progress.json").exists() and not (meeting / cli.STOP_FILE).exists()
    assert "2 of 2 part(s) transcribed" in capsys.readouterr().out


def test_cli_ends_with_the_quota_reason_as_its_last_line(monkeypatch, meeting, capsys):
    class Exhausted(FakeTranscriber):
        exhausted = "Gemini's daily limit for fake is used up; it resets in 5 h 00 min."
    fake = Exhausted(QuotaExhausted(Exhausted.exhausted), QuotaExhausted(Exhausted.exhausted))
    monkeypatch.setitem(base.TRANSCRIBERS, "fake", lambda: fake)
    monkeypatch.setattr(sys, "argv", ["transcribe", str(meeting), "--only-failed", "--transcriber", "fake",
                                      "--glossary", str(meeting / "none.txt")])
    with pytest.raises(SystemExit) as done:
        cli.main()
    assert done.value.code == 1
    assert capsys.readouterr().out.strip().splitlines()[-1].endswith("it resets in 5 h 00 min.")


def test_a_whole_run_that_is_worse_keeps_the_earlier_transcript(monkeypatch, meeting, capsys):
    before = (meeting / "transcript.json").read_text(encoding="utf-8")  # 2 parts missing
    code, _ = run_cli(monkeypatch, meeting, answers=[RuntimeError("429")] * 6)  # all 3 parts fail this time
    assert code == 1
    assert code == 1
    assert (meeting / "transcript.json").read_text(encoding="utf-8") == before
    assert "Kept the earlier transcript" in capsys.readouterr().out


def test_a_write_racing_close_does_not_bring_the_file_back(tmp_path):
    p = Progress(tmp_path / "progress.json", every=60)
    p.close()
    p._write()  # the ticker thread waking up just after close()
    p.part_done(failed=False)
    assert not (tmp_path / "progress.json").exists()


def test_the_last_line_explains_empty_answers_in_plain_words(tmp_path):
    segments = [pipeline.Segment("Me", 0, 5, failed="empty transcript"), pipeline.Segment("Me", 6, 11, failed="empty transcript")]
    pipeline.write(segments, 11, "fake", "t", tmp_path / "transcript")
    assert cli._main_reason(tmp_path / "transcript").startswith("Most of them came back with no words")
    pipeline.write([pipeline.Segment("Me", 0, 5, failed="ServerError: 503")], 5, "fake", "t", tmp_path / "transcript")
    assert cli._main_reason(tmp_path / "transcript") == "It failed with: ServerError: 503"
