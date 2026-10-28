"""Speaker identification without the model: splitting segments at speaker changes,
the after-meeting and live paths with a fake diarizer, renaming, and failing safe."""
import json
from types import SimpleNamespace

import huggingface_hub
import numpy as np
import pytest
from huggingface_hub.errors import GatedRepoError

from transcriber import cli, live, models, pipeline, speakers
from tests.test_audio import make_audio
from tests.test_live import encode, quiet, tone, wav_header
from tests.test_pipeline import FakeTranscriber


def test_split_keeps_one_speaker_whole():
    assert speakers.split(2.0, 6.0, [(1.8, 6.3, "Speaker 1")], "Others") == [(2.0, 6.0, "Speaker 1")]


def test_split_cuts_where_the_speaker_changes_and_covers_the_whole_segment():
    turns = [(2.1, 4.0, "Speaker 1"), (4.4, 7.0, "Speaker 2"), (7.2, 9.8, "Speaker 1")]
    assert speakers.split(2.0, 10.0, turns, "Others") == [
        (2.0, 4.4, "Speaker 1"), (4.4, 7.2, "Speaker 2"), (7.2, 10.0, "Speaker 1")]


def test_split_folds_a_very_short_turn_into_its_neighbour():
    turns = [(0.0, 3.0, "Speaker 1"), (3.0, 3.3, "Speaker 2"), (3.3, 6.0, "Speaker 1")]
    assert speakers.split(0.0, 6.0, turns, "Others") == [(0.0, 6.0, "Speaker 1")]


def test_split_without_a_turn_inside_takes_the_nearest_speaker_or_the_fallback():
    turns = [(0.0, 2.0, "Speaker 1"), (9.0, 12.0, "Speaker 2")]
    assert speakers.split(7.0, 8.0, turns, "Others") == [(7.0, 8.0, "Speaker 2")]
    assert speakers.split(7.0, 8.0, [], "Others") == [(7.0, 8.0, "Others")]


def test_after_the_meeting_others_is_split_into_speakers(tmp_path):
    mic = make_audio(tmp_path / "mic.wav", [("tone", 3), ("quiet", 9)])
    system = make_audio(tmp_path / "system.wav", [("quiet", 4), ("tone", 6), ("quiet", 2)])  # one region, 4-10 s
    asked = []

    def diarize(wav):
        asked.append(wav)
        return [(4.0, 7.0, "Speaker 1"), (7.0, 10.0, "Speaker 2")]

    t = FakeTranscriber("আমি", "first", "second")
    segments, _ = pipeline.transcribe_tracks({mic: "Me", system: "Others"}, t, [], workers=1, diarize=diarize)
    assert len(asked) == 1, "only the Others track is diarized"
    assert [(s.speaker, s.track, s.text) for s in segments] == [
        ("Me", "mic", "আমি"), ("Speaker 1", "system", "first"), ("Speaker 2", "system", "second")]
    assert segments[1].end == segments[2].start == 7.0


def test_live_relabels_one_speaker_chunks_and_retranscribes_only_mixed_ones(tmp_path):
    folder = tmp_path / "meeting"
    (folder / "live").mkdir(parents=True)
    rate = 48_000
    # Others: 4-7 s and 8-10 s are one chunk (pause < 2 s) but two people; 14-16 s is one person.
    system_audio = np.concatenate([quiet(4, rate), tone(3, rate), quiet(1, rate), tone(2, rate),
                                   quiet(4, rate), tone(2, rate), quiet(2, rate)])
    mic_audio = np.concatenate([tone(2, rate), quiet(16, rate)])
    (folder / "live" / "system.wav").write_bytes(wav_header(rate, 1, float32=True) + encode(system_audio, 1, True))
    (folder / "live" / "mic.wav").write_bytes(wav_header(rate, 1, float32=True) + encode(mic_audio, 1, True))
    (folder / "live" / "done").touch()
    turns = [(4.0, 7.0, "Speaker 1"), (8.0, 10.0, "Speaker 2"), (14.0, 16.0, "Speaker 1")]
    t = FakeTranscriber()

    failed = live.run(folder, cli.SPEAKERS, t, [], workers=1, poll=0.01, diarize=lambda wav: turns)

    data = json.loads((folder / "transcript.json").read_text(encoding="utf-8"))
    assert failed == 0
    assert [(s["speaker"], s["track"]) for s in data["segments"]] == [
        ("Me", "mic"), ("Speaker 1", "system"), ("Speaker 2", "system"), ("Speaker 1", "system")]
    assert len(t.glossaries) == 5, "mic + 2 live chunks + the 2 pieces of the mixed chunk"
    assert "Others" not in (folder / "transcript.md").read_text(encoding="utf-8")


def test_rename_speaker_rewrites_json_and_markdown_and_can_merge(tmp_path):
    out = tmp_path / "transcript"
    pipeline.write([pipeline.Segment("Speaker 1", 0, 2, "hello", track="system"),
                    pipeline.Segment("Speaker 2", 3, 5, "hi", track="system"),
                    pipeline.Segment("Speaker 3", 6, 8, "again", track="system")], 8.0, "fake", "Meeting", out)
    assert pipeline.rename_speaker(out, "Meeting", "Speaker 2", "  Rahim   Uddin ") == 1
    assert pipeline.rename_speaker(out, "Meeting", "Speaker 3", "Speaker 1") == 1
    data = json.loads((tmp_path / "transcript.json").read_text(encoding="utf-8"))
    assert [(s["speaker"], s["track"]) for s in data["segments"]] == [
        ("Speaker 1", "system"), ("Rahim Uddin", "system"), ("Speaker 1", "system")]
    assert "**[00:00:03] Rahim Uddin:** hi" in (tmp_path / "transcript.md").read_text(encoding="utf-8")
    with pytest.raises(SystemExit):
        pipeline.rename_speaker(out, "Meeting", "Speaker 9", "Karim")
    with pytest.raises(SystemExit):
        pipeline.rename_speaker(out, "Meeting", "Speaker 1", "   ")


def test_old_transcripts_without_track_can_still_be_renamed(tmp_path):
    (tmp_path / "transcript.json").write_text(json.dumps({"model": "m", "duration": 2.0, "segments": [
        {"speaker": "Others", "start": 0, "end": 2, "text": "x", "failed": ""}]}), encoding="utf-8")
    assert pipeline.rename_speaker(tmp_path / "transcript", "Meeting", "Others", "Karim") == 1


def test_speaker_identification_never_costs_the_transcript(monkeypatch, capsys):
    monkeypatch.setattr(speakers, "available", lambda: False)
    assert cli._diarizer(True) is None
    assert "not downloaded" in capsys.readouterr().out
    monkeypatch.setattr(speakers, "available", lambda: True)

    def broken(wav):
        raise RuntimeError("MPS out of memory")
    monkeypatch.setattr(speakers, "diarize", broken)
    assert cli._diarizer(True)("x.wav") == []
    assert "keeping “Others”" in capsys.readouterr().out
    assert cli._diarizer(False) is None


def test_speaker_model_is_listed_and_a_refused_download_says_what_to_do(monkeypatch):
    assert models.SPEAKERS_ID in {m["id"] for m in models.status()}

    def refused(repo, f, dry_run=False, tqdm_class=None):
        raise GatedRepoError("gated", response=SimpleNamespace(status_code=403, headers={}, request=None))
    monkeypatch.setattr(huggingface_hub, "hf_hub_download", refused)
    with pytest.raises(SystemExit, match="accept the terms at https://huggingface.co/pyannote/speaker-diarization-community-1"):
        models.download(models.SPEAKERS, lambda done, total: None)


def test_pyannote_telemetry_is_off_and_matplotlib_stays_out_of_home():
    import os

    assert os.environ["PYANNOTE_METRICS_ENABLED"] == "false"
    assert os.environ.get("MPLCONFIGDIR"), "matplotlib's cache must not default to ~/.matplotlib"
    pytest.importorskip("pyannote.audio")
    from pyannote.audio.telemetry.metrics import is_metrics_enabled

    assert not is_metrics_enabled()
