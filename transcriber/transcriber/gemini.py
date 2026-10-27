"""Gemini transcription models (gemini-*-transcribe).

Tested 2026-10-26 on a Bangla/English clip: custom_vocabulary makes English
terms come out in English letters ("API", "deploy"); without it they are
spelled in Bangla script. Diarization cannot be combined with the vocabulary,
so speakers come from separate tracks instead."""
import os
from pathlib import Path

from google import genai
from google.genai import types

# Gemini recommends up to ~100 terms for best results; it accepts 1,000.
MAX_TERMS = 1000


class GeminiTranscriber:
    def __init__(self, model: str, languages: tuple[str, ...] = ("bn-BD", "en-US")):
        self.name = model
        self.languages = list(languages)
        key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not key:
            raise SystemExit("No Gemini API key: add it in AnyMeet → Settings → Transcription")
        self.client = genai.Client(
            api_key=key,
            http_options=types.HttpOptions(
                timeout=120_000,  # ms; a stuck request fails instead of hanging the run
                retry_options=types.HttpRetryOptions(
                    attempts=6, initial_delay=5, max_delay=60,
                    http_status_codes=[429, 500, 502, 503, 504],
                ),
            ),
        )

    def transcribe(self, audio: Path, glossary: list[str]) -> str:
        config = types.AudioTranscriptionConfig(
            language_codes=self.languages,
            custom_vocabulary=glossary[:MAX_TERMS] or None,
        )
        resp = self.client.models.generate_content(
            model=self.name,
            contents=[types.Part.from_bytes(data=audio.read_bytes(), mime_type="audio/flac")],
            config=types.GenerateContentConfig(audio_transcription_config=config),
        )
        # The transcript arrives in audio_transcription parts, not text parts.
        parts = resp.candidates[0].content.parts if resp.candidates and resp.candidates[0].content else []
        return "".join(p.audio_transcription.text or "" for p in parts if p.audio_transcription)
