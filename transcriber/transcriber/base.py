"""The adapter contract. A provider only turns one short audio file into text;
splitting, retrying on empty output, ordering and output files live in the
pipeline, so a new provider is one small class plus one registry line."""
from pathlib import Path
from typing import Callable, Protocol

from . import models


class Transcriber(Protocol):
    name: str

    def transcribe(self, audio: Path, glossary: list[str]) -> str:
        """Return the verbatim transcript of a short (<= 60 s) FLAC file.
        Raise on failure; the pipeline records it instead of dropping the audio."""
        ...


def _gemini_35() -> Transcriber:
    from .gemini import GeminiTranscriber

    return GeminiTranscriber("gemini-3.5-transcribe")


def _local(name: str) -> Callable[[], Transcriber]:
    def make() -> Transcriber:
        from .local_whisper import LocalWhisperTranscriber

        return LocalWhisperTranscriber(name, models.LOCAL[name])
    return make


# Imports stay inside the factories so only the chosen provider's SDK loads.
TRANSCRIBERS: dict[str, Callable[[], Transcriber]] = {
    "gemini-3.5-transcribe": _gemini_35,
    **{name: _local(name) for name in models.LOCAL},
}
DEFAULT = "gemini-3.5-transcribe"


def get(name: str) -> Transcriber:
    if name not in TRANSCRIBERS:
        raise SystemExit(f"unknown transcriber {name!r}; available: {', '.join(TRANSCRIBERS)}")
    return TRANSCRIBERS[name]()
