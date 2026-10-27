"""Local model status and downloads, as the apps' Settings page uses them (no network)."""
import json
import sys
from types import SimpleNamespace

import huggingface_hub
import pytest

from transcriber import base, cli, models


def test_every_local_model_is_a_transcriber():
    assert set(models.LOCAL) <= set(base.TRANSCRIBERS)
    assert models.LOCAL["local-whisper-english"].language == "english"
    assert ("openai/whisper-medium", "generation_config.json") in models.LOCAL["local-whisper-bangla"].files()


def test_downloaded_needs_every_file(monkeypatch):
    model = models.LOCAL["local-whisper-english"]
    cached = {f for _, f in model.files()}
    monkeypatch.setattr(huggingface_hub, "try_to_load_from_cache", lambda repo, f: f"/cache/{f}" if f in cached else None)
    assert models.downloaded(model)
    cached.discard("model.safetensors")
    assert not models.downloaded(model)


def test_download_reports_bytes_from_cached_to_total(monkeypatch):
    model = models.LOCAL["local-whisper-english"]
    sizes = {f: 10 for _, f in model.files()} | {"model.safetensors": 1000}
    cached = set(sizes) - {"model.safetensors", "vocab.json"}
    fetched = []

    def fake_download(repo, f, dry_run=False, tqdm_class=None):
        if dry_run:
            return SimpleNamespace(file_size=sizes[f], will_download=f not in cached)
        fetched.append(f)
        bar = tqdm_class(total=sizes[f], disable=True)  # hub may open more than one bar per file
        for _ in range(4):
            bar.n += sizes[f] // 4
            bar.update(0)
        tqdm_class(total=sizes[f], disable=True).update(0)
        return f"/cache/{f}"

    monkeypatch.setattr(huggingface_hub, "hf_hub_download", fake_download)
    reports = []
    models.download(model, lambda done, total: reports.append((done, total)))
    total = sum(sizes.values())
    assert sorted(fetched) == ["model.safetensors", "vocab.json"]
    assert reports[0] == (total - 1010, total) and reports[-1] == (total, total)
    assert [d for d, _ in reports] == sorted(d for d, _ in reports), "progress never goes back"


def test_cli_models_prints_json(monkeypatch, capsys):
    monkeypatch.setattr(models, "downloaded", lambda m: m.language == "bengali")
    monkeypatch.setattr(sys, "argv", ["transcribe", "--models"])
    cli.main()
    assert json.loads(capsys.readouterr().out) == [
        {"id": "local-whisper-bangla", "downloaded": True},
        {"id": "local-whisper-english", "downloaded": False},
    ]


def test_cli_rejects_unknown_download(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["transcribe", "--download", "gemini-3.5-transcribe"])
    with pytest.raises(SystemExit):
        cli.main()


def test_progress_printer_throttles_but_always_prints_the_end(capsys):
    report = cli._progress_printer(every=60)
    for done in range(0, 100, 10):
        report(done, 100)
    report(100, 100)
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert lines == [{"done": 0, "total": 100}, {"done": 100, "total": 100}]
