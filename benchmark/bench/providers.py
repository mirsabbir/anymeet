"""One function per provider: (wav path) -> transcript text.

Request formats and prices were taken from each provider's docs in October 2026.
Prices are list prices per audio hour and only used for the cost column."""
import functools
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import requests

TIMEOUT = 300
PROMPT = (
    "Transcribe this audio verbatim. Speakers mix Bangla and English. Write Bangla "
    "words in Bangla script and English words in English letters, exactly as spoken. "
    "Do not translate, summarize, correct or add anything. Output only the transcript."
)


@dataclass(frozen=True)
class Provider:
    name: str
    transcribe: Callable[[Path], str]
    key_env: str | None
    max_seconds: float | None  # longer audio is split at silences first
    usd_per_hour: float | None


def _key(env: str) -> str:
    return os.environ[env]


def _ok(resp: requests.Response) -> requests.Response:
    if resp.status_code >= 400:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
    return resp


def sarvam(model: str, mode: str | None, wav: Path) -> str:
    data = {"model": model, "language_code": "bn-IN"}
    if mode:
        data["mode"] = mode
    with wav.open("rb") as f:
        resp = requests.post(
            "https://api.sarvam.ai/speech-to-text",
            headers={"api-subscription-key": _key("SARVAM_API_KEY")},
            files={"file": (wav.name, f, "audio/wav")}, data=data, timeout=TIMEOUT,
        )
    return _ok(resp).json()["transcript"]


def soniox(wav: Path) -> str:
    base, s = "https://api.soniox.com/v1", requests.Session()
    s.headers["Authorization"] = f"Bearer {_key('SONIOX_API_KEY')}"
    with wav.open("rb") as f:
        file_id = _ok(s.post(f"{base}/files", files={"file": f}, timeout=TIMEOUT)).json()["id"]
    try:
        body = {"model": "stt-async-v5", "file_id": file_id, "language_hints": ["bn", "en"]}
        tid = _ok(s.post(f"{base}/transcriptions", json=body, timeout=TIMEOUT)).json()["id"]
        try:
            while (job := _ok(s.get(f"{base}/transcriptions/{tid}", timeout=TIMEOUT)).json())["status"] not in ("completed", "error"):
                time.sleep(1)
            if job["status"] == "error":
                raise RuntimeError(job.get("error_message", "soniox error"))
            tokens = _ok(s.get(f"{base}/transcriptions/{tid}/transcript", timeout=TIMEOUT)).json()["tokens"]
            return "".join(t["text"] for t in tokens)
        finally:
            s.delete(f"{base}/transcriptions/{tid}", timeout=TIMEOUT)
    finally:
        s.delete(f"{base}/files/{file_id}", timeout=TIMEOUT)


def elevenlabs(wav: Path) -> str:
    with wav.open("rb") as f:
        resp = requests.post(
            "https://api.elevenlabs.io/v1/speech-to-text",
            headers={"xi-api-key": _key("ELEVENLABS_API_KEY")},
            files={"file": (wav.name, f, "audio/wav")},
            data={"model_id": "scribe_v2", "language_code": "ben", "tag_audio_events": "false"},
            timeout=TIMEOUT,
        )
    return _ok(resp).json()["text"]


def deepgram(wav: Path) -> str:
    resp = requests.post(
        "https://api.deepgram.com/v1/listen",
        params={"model": "nova-3", "language": "bn", "smart_format": "true"},
        headers={"Authorization": f"Token {_key('DEEPGRAM_API_KEY')}", "Content-Type": "audio/wav"},
        data=wav.read_bytes(), timeout=TIMEOUT,
    )
    return _ok(resp).json()["results"]["channels"][0]["alternatives"][0]["transcript"]


def openai(model: str, wav: Path) -> str:
    with wav.open("rb") as f:
        resp = requests.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {_key('OPENAI_API_KEY')}"},
            files={"file": (wav.name, f, "audio/wav")},
            data={"model": model, "language": "bn", "prompt": PROMPT}, timeout=TIMEOUT,
        )
    return _ok(resp).json()["text"]


def gemini(model: str, wav: Path) -> str:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=_key("GEMINI_API_KEY"))
    resp = client.models.generate_content(
        model=model,
        contents=[types.Part.from_bytes(data=wav.read_bytes(), mime_type="audio/wav"), PROMPT],
    )
    # gemini-*-transcribe answers in audio_transcription parts, not text parts
    parts = resp.candidates[0].content.parts if resp.candidates else []
    return "".join((p.audio_transcription.text if p.audio_transcription else p.text) or "" for p in parts)


@functools.cache
def _whisper_pipeline(model: str, base: str | None):
    import torch
    from transformers import GenerationConfig, pipeline

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    pipe = pipeline("automatic-speech-recognition", model=model, device=device,
                    torch_dtype=torch.float16 if device == "mps" else torch.float32)
    if base:  # older fine-tunes ship a generation config without language tokens
        pipe.model.generation_config = GenerationConfig.from_pretrained(base)
    return pipe


def local_whisper(model: str, base: str | None, wav: Path) -> str:
    pipe = _whisper_pipeline(model, base)
    return pipe(str(wav), generate_kwargs={"language": "bengali", "task": "transcribe"})["text"]


P = functools.partial
PROVIDERS = {p.name: p for p in [
    # Sarvam's REST endpoint is meant for clips under 30 s; we split, it stays sync.
    Provider("sarvam-v4", P(sarvam, "saaras:v4", None), "SARVAM_API_KEY", 28, 0.31),
    Provider("sarvam-v3-codemix", P(sarvam, "saaras:v3", "codemix"), "SARVAM_API_KEY", 28, 0.31),
    Provider("soniox", soniox, "SONIOX_API_KEY", None, 0.10),
    Provider("elevenlabs", elevenlabs, "ELEVENLABS_API_KEY", None, 0.22),
    Provider("deepgram", deepgram, "DEEPGRAM_API_KEY", None, 0.26),
    # 25 MB upload cap is ~13 min of 16 kHz WAV.
    Provider("openai-gpt-transcribe", P(openai, "gpt-transcribe"), "OPENAI_API_KEY", 600, 0.27),
    # Inline audio is capped at 20 MB per request (~10 min of 16 kHz WAV).
    Provider("gemini-3.5-transcribe", P(gemini, "gemini-3.5-transcribe"), "GEMINI_API_KEY", 600, None),
    Provider("gemini-3.8-flash", P(gemini, "gemini-3.8-flash"), "GEMINI_API_KEY", 600, None),
    Provider("local-bengaliai-whisper", P(local_whisper, "bengaliAI/tugstugi_bengaliai-asr_whisper-medium", "openai/whisper-medium"), None, 28, 0.0),
    Provider("local-whisper-turbo", P(local_whisper, "openai/whisper-large-v3-turbo", None), None, 28, 0.0),
]}


def available(p: Provider) -> bool:
    return p.key_env is None or bool(os.environ.get(p.key_env))


def describe() -> str:
    return json.dumps({n: ("ready" if available(p) else f"needs {p.key_env}") for n, p in PROVIDERS.items()}, indent=2)
