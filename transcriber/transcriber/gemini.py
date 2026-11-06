"""Gemini transcription: the gemini-*-transcribe models, and a general Flash model
told to transcribe.

Tested 2026-10-26 on a Bangla/English clip: custom_vocabulary makes English
terms come out in English letters ("API", "deploy"); without it they are
spelled in Bangla script. Diarization cannot be combined with the vocabulary,
so speakers come from separate tracks instead.

Quotas (measured 2026-11-06 on a Tier 1 key): gemini-3.5-transcribe allows only
100 requests a day per project. A meeting is cut into hundreds of parts, so that
limit runs out within one meeting, and every later request fails with 429 until
the next day. Retrying then only burns time, so a daily limit stops the run at
once (QuotaExhausted) and says when it resets. A per-minute limit is waited out
by every worker together (RateGate), instead of each one hammering on its own.
The general Flash models have far larger daily limits."""
import os
import re
import threading
import time
from pathlib import Path
from typing import Callable

from google import genai
from google.genai import errors, types

from .base import QuotaExhausted

# Gemini recommends up to ~100 terms for best results; it accepts 1,000.
MAX_TERMS = 1000
# A 429 that asks for a longer wait than this is a daily (or longer) limit: stop, don't wait.
LONGEST_WAIT = 120.0
ATTEMPTS = 6  # per part, for per-minute limits

FLASH_PROMPT = (
    "Transcribe this audio clip verbatim, in the language it is spoken: Bangla in Bangla script, "
    "English in English letters, and mixed sentences stay mixed.{terms} "
    "Return only the spoken words: no speaker labels, timestamps, notes, descriptions or translation. "
    "If nobody speaks, return nothing."
)


class RateGate:
    """Shared by all workers. After a 429 that says "retry in N s", nobody sends until then."""

    def __init__(self, clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        self.clock, self.sleep = clock, sleep
        self.open_at = 0.0
        self.lock = threading.Lock()

    def close_for(self, seconds: float) -> None:
        with self.lock:
            self.open_at = max(self.open_at, self.clock() + seconds)

    def waiting(self) -> float:
        """Seconds until requests may go out again; 0 when open."""
        with self.lock:
            return max(0.0, self.open_at - self.clock())

    def wait(self, stop: threading.Event | None = None) -> None:
        while (left := self.waiting()) > 0 and not (stop and stop.is_set()):
            self.sleep(min(left, 1.0))


def _seconds(text: str) -> float | None:
    """'53956s', '23.5s' or '14h59m16.2s' -> seconds."""
    parts = re.findall(r"([\d.]+)\s*([hms])", text)
    if not parts:
        return None
    return sum(float(n) * {"h": 3600, "m": 60, "s": 1}[u] for n, u in parts)


def rate_limit(e: errors.APIError) -> tuple[bool, float | None, str]:
    """For a 429: (is it a daily limit, how long Gemini asks us to wait, the limit in words)."""
    details = e.details.get("error", {}).get("details", []) if isinstance(e.details, dict) else []
    daily, wait, what = False, None, ""
    for d in details:
        kind = d.get("@type", "")
        if kind.endswith("QuotaFailure"):
            for v in d.get("violations", []):
                quota = v.get("quotaId", "") + v.get("quotaMetric", "")
                per_day = "PerDay" in quota or "per_day" in quota
                daily = daily or per_day
                if v.get("quotaValue"):
                    what = f"{v['quotaValue']} requests a {'day' if per_day else 'minute'}"
        elif kind.endswith("RetryInfo"):
            wait = _seconds(d.get("retryDelay", ""))
    if wait is None:
        found = re.search(r"retry in ([\d.hms]+)", str(e.message or ""))
        wait = _seconds(found.group(1)) if found else None
    daily = daily or "per day" in str(e.message or "").lower()
    return daily or (wait is not None and wait > LONGEST_WAIT), wait, what


def _clock(seconds: float) -> str:
    h, m = int(seconds // 3600), int(seconds % 3600 // 60)
    return f"{h} h {m:02d} min" if h else f"{max(m, 1)} min"


class _Gemini:
    """What every Gemini model shares: the client, the shared gate, and stopping at a daily limit."""

    def __init__(self, model: str):
        self.name = model
        key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not key:
            raise SystemExit("No Gemini API key: add it in AnyMeet → Settings → Transcription")
        self.client = genai.Client(
            api_key=key,
            http_options=types.HttpOptions(
                timeout=120_000,  # ms; a stuck request fails instead of hanging the run
                # 429 is handled in _send, for all workers at once; the SDK retries only server errors.
                retry_options=types.HttpRetryOptions(attempts=4, initial_delay=5, max_delay=60,
                                                     http_status_codes=[500, 502, 503, 504]),
            ),
        )
        self.gate = RateGate()
        self.stop: threading.Event | None = None  # the pipeline's stop flag: stop waiting once the run is stopped
        self.exhausted = ""  # why every further request would fail, once a daily limit is hit

    def waiting(self) -> float:
        """Seconds all workers are holding back for a per-minute limit; the pipeline reports it."""
        return self.gate.waiting()

    def _send(self, request: Callable[[], str]) -> str:
        for attempt in range(ATTEMPTS):
            if self.exhausted:
                raise QuotaExhausted(self.exhausted)
            self.gate.wait(self.stop)
            try:
                return request()
            except errors.APIError as e:
                if e.code != 429:
                    raise
                daily, wait, what = rate_limit(e)
                if daily:
                    resets = f"; it resets in {_clock(wait)}" if wait else ""
                    self.exhausted = (f"Gemini's daily limit for {self.name} is used up ({what or 'requests a day'} "
                                      f"on this API key){resets}. Try again then, or choose another model in "
                                      f"Settings → Transcription.")
                    raise QuotaExhausted(self.exhausted) from None
                if attempt == ATTEMPTS - 1:
                    raise
                self.gate.close_for(wait or 5 * 2 ** attempt)
        raise AssertionError("unreachable")


class GeminiTranscriber(_Gemini):
    """gemini-*-transcribe: the dedicated speech-to-text models, with a custom vocabulary."""

    def __init__(self, model: str, languages: tuple[str, ...] = ("bn-BD", "en-US")):
        super().__init__(model)
        self.languages = list(languages)

    def transcribe(self, audio: Path, glossary: list[str]) -> str:
        config = types.AudioTranscriptionConfig(
            language_codes=self.languages,
            custom_vocabulary=glossary[:MAX_TERMS] or None,
        )

        def request() -> str:
            resp = self.client.models.generate_content(
                model=self.name,
                contents=[types.Part.from_bytes(data=audio.read_bytes(), mime_type="audio/flac")],
                config=types.GenerateContentConfig(audio_transcription_config=config),
            )
            # The transcript arrives in audio_transcription parts, not text parts.
            parts = resp.candidates[0].content.parts if resp.candidates and resp.candidates[0].content else []
            return "".join(p.audio_transcription.text or "" for p in parts if p.audio_transcription)
        return self._send(request)


class GeminiFlashTranscriber(_Gemini):
    """A general Gemini Flash model asked to transcribe. Spot-checked 2026-11-06 on clips of
    a mixed Bangla/English meeting: the same words as gemini-3.5-transcribe, nothing for
    silence, and a far larger daily limit. Not yet in benchmark/."""

    def transcribe(self, audio: Path, glossary: list[str]) -> str:
        terms = glossary[:MAX_TERMS]
        prompt = FLASH_PROMPT.format(
            terms=f" Spell these terms exactly like this when they are said: {', '.join(terms)}." if terms else "")

        def request() -> str:
            resp = self.client.models.generate_content(
                model=self.name,
                contents=[types.Part.from_bytes(data=audio.read_bytes(), mime_type="audio/flac"), prompt],
                config=types.GenerateContentConfig(temperature=0),
            )
            return resp.text or ""
        return self._send(request)
