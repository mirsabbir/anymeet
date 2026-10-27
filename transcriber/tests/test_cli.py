"""Command-line behaviour that the desktop apps rely on."""
import sys

import pytest

from transcriber import cli
from transcriber.gemini import GeminiTranscriber


def test_tracks_in_a_meeting_folder(tmp_path):
    for name in ["mic.m4a", "system.m4a", "display-1.mp4", "notes.txt"]:
        (tmp_path / name).write_bytes(b"")
    tracks, out = cli._tracks(tmp_path)
    assert {p.name: who for p, who in tracks.items()} == {"mic.m4a": "Me", "system.m4a": "Others"}
    assert out == tmp_path / "transcript"


def test_single_file_is_one_speaker(tmp_path):
    f = tmp_path / "talk.m4a"
    f.write_bytes(b"")
    tracks, out = cli._tracks(f)
    assert list(tracks.values()) == ["Speaker"]
    assert out.name == "talk.transcript"


def test_glossary_skips_comments_and_blank_lines(tmp_path):
    g = tmp_path / "glossary.txt"
    g.write_text("# comment\nAPI\n\n  deploy  \n", encoding="utf-8")
    assert cli._glossary(g) == ["API", "deploy"]
    assert cli._glossary(tmp_path / "missing.txt") == []


def test_settings_folder_matches_the_apps(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert cli.config_dir().parts[-3:] == ("Library", "Application Support", "AnyMeet")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert cli.config_dir() == tmp_path / "AnyMeet"


def test_missing_gemini_key_gives_a_clear_message(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="Settings → Transcription"):
        GeminiTranscriber("gemini-3.5-transcribe")
