"""<meeting>/summary.json: what a meeting was about, written by Claude from its transcript.

Needs ANTHROPIC_API_KEY (Settings → AI summary). The transcript goes to Claude Opus 5.5 in one
streamed request, and the answer is held to SCHEMA with structured outputs, so the apps can show
it without parsing prose. A one-hour meeting is roughly 30–80 thousand tokens in, a few thousand
out: about $0.20–0.50 at Opus 5.5's $4 / $20 per million tokens.

{"title": "…", "overview": "…", "decisions": ["…"], "actionItems": [{"owner", "task", "due"}],
 "topics": [{"start": 754.2, "title": "…", "summary": "…"}], "openQuestions": ["…"],
 "language": "auto", "model": "claude-opus-5-5", "createdAt": "2026-11-11T16:20:00Z"}"""
import json
import os
import time
from pathlib import Path

import anthropic

FILE = "summary.json"
MODEL = "claude-opus-5-5"
LANGUAGES = {
    "auto": "Write in the language most of the meeting was spoken in. If it is mostly Bangla, write Bangla in Bangla "
            "script and keep English terms, names and product words in English letters, as the speakers said them.",
    "en": "Write in English, whatever language the meeting was in.",
    "bn": "Write in Bangla, in Bangla script. Keep English terms, names and product words in English letters.",
}
SYSTEM = """You write the summary of a recorded meeting for the person who recorded it, who is "Me" in the transcript.
The transcript has one line per part: [HH:MM:SS] Speaker: text. It comes from speech recognition, so expect misheard
words; read them by context and never invent what nobody said. "Others" means the people on the call when they could
not be told apart.

- title: a few words naming what the meeting was about, not the date.
- overview: two to four sentences: what it was for and what came out of it.
- decisions: what was agreed, one sentence each; empty if nothing was decided.
- actionItems: who does what by when. owner is the speaker's name as in the transcript; due is "" when nobody said.
- topics: the meeting in order, a handful of sections; start is where the section begins, in seconds.
- openQuestions: what was left unanswered or needs a follow-up; empty if nothing.
Be concise and concrete. Skip greetings and small talk unless they matter to the work."""
SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "overview": {"type": "string"},
        "decisions": {"type": "array", "items": {"type": "string"}},
        "actionItems": {"type": "array", "items": {
            "type": "object",
            "properties": {"owner": {"type": "string"}, "task": {"type": "string"}, "due": {"type": "string"}},
            "required": ["owner", "task", "due"], "additionalProperties": False}},
        "topics": {"type": "array", "items": {
            "type": "object",
            "properties": {"start": {"type": "number"}, "title": {"type": "string"}, "summary": {"type": "string"}},
            "required": ["start", "title", "summary"], "additionalProperties": False}},
        "openQuestions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "overview", "decisions", "actionItems", "topics", "openQuestions"],
    "additionalProperties": False,
}


class SummaryError(Exception):
    """Why there is no summary, in words the user can act on."""


def transcript_text(folder: Path) -> str:
    try:
        segments = json.loads((folder / "transcript.json").read_text(encoding="utf-8"))["segments"]
    except (OSError, ValueError, KeyError):
        raise SummaryError("There is no transcript to summarize yet.") from None
    lines = [f"[{_clock(s['start'])}] {s['speaker']}: {s['text']}" for s in segments if s.get("text") and not s.get("failed")]
    if not lines:
        raise SummaryError("The transcript has no words to summarize.")
    return "\n".join(lines)


def summarize(folder: Path, language: str = "auto", client: anthropic.Anthropic | None = None) -> dict:
    """Writes <folder>/summary.json and returns it."""
    if not (client or os.environ.get("ANTHROPIC_API_KEY")):
        raise SummaryError("Add your Anthropic API key in Settings → AI summary.")
    text = transcript_text(folder)
    client = client or anthropic.Anthropic(max_retries=4)
    try:
        with client.beta.messages.stream(
            model=MODEL,
            max_tokens=32000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",  # if Opus 5.5 declines, the API retries on a fallback model in the same call
            output_config={"effort": "high", "format": {"type": "json_schema", "schema": SCHEMA}},
            system=f"{SYSTEM}\n\n{LANGUAGES.get(language, LANGUAGES['auto'])}",
            messages=[{"role": "user", "content": text}],
        ) as stream:
            message = stream.get_final_message()
    except anthropic.AuthenticationError:
        raise SummaryError("Anthropic refused the API key: check it in Settings → AI summary.") from None
    except anthropic.RateLimitError:
        raise SummaryError("Anthropic's rate limit was reached: summarize again in a minute.") from None
    except anthropic.APIConnectionError:
        raise SummaryError("No connection to Anthropic: summarize again when you are online.") from None
    except anthropic.APIStatusError as e:
        raise SummaryError(f"Anthropic said: {e.message}") from None
    summary = {**_parse(message), "language": language, "model": message.model,
               "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    tmp = folder / (FILE + ".tmp")
    tmp.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, folder / FILE)
    return summary


def _parse(message) -> dict:
    if message.stop_reason == "refusal":
        raise SummaryError("Claude declined to summarize this meeting.")
    if message.stop_reason == "max_tokens":
        raise SummaryError("The summary came out too long and was cut off; summarize again.")
    text = next((b.text for b in message.content if b.type == "text"), "")
    try:
        return json.loads(text)
    except ValueError:
        raise SummaryError("Claude's answer wasn't a summary; summarize again.") from None


def _clock(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"
