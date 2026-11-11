"""The summary request and what is written, with a stand-in for the Anthropic client."""
import json
from types import SimpleNamespace

import pytest

from transcriber import summary

ANSWER = {"title": "Release plan", "overview": "The team agrees to test before the demo.", "decisions": ["Test first"],
          "actionItems": [{"owner": "Me", "task": "Write the runbook", "due": "Friday"}],
          "topics": [{"start": 0, "title": "Start", "summary": "Tracker sheet"}], "openQuestions": []}


class FakeClient:
    def __init__(self, text: str, stop_reason: str = "end_turn"):
        self.sent, self.message = {}, SimpleNamespace(
            stop_reason=stop_reason, model=summary.MODEL, content=[SimpleNamespace(type="text", text=text)])
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self.stream))

    def stream(self, **kw):
        self.sent = kw
        client = self

        class Stream:
            def __enter__(self):
                return SimpleNamespace(get_final_message=lambda: client.message)

            def __exit__(self, *exc):
                return False
        return Stream()


@pytest.fixture
def meeting(tmp_path):
    (tmp_path / "transcript.json").write_text(json.dumps({"segments": [
        {"speaker": "Me", "start": 3.2, "end": 5, "text": "শুরু করি", "failed": ""},
        {"speaker": "Others", "start": 3725, "end": 3730, "text": "", "failed": "empty transcript"},
        {"speaker": "Rahim", "start": 3730, "end": 3740, "text": "deploy the API", "failed": ""}]}), encoding="utf-8")
    return tmp_path


def test_sends_the_transcript_and_writes_the_summary(meeting):
    client = FakeClient(json.dumps(ANSWER))
    written = summary.summarize(meeting, "bn", client=client)
    assert client.sent["messages"][0]["content"] == "[00:00:03] Me: শুরু করি\n[01:02:10] Rahim: deploy the API"
    assert client.sent["model"] == "claude-opus-5-5" and client.sent["fallbacks"] == "default"
    assert client.sent["output_config"]["format"]["schema"] is summary.SCHEMA
    assert "Bangla script" in client.sent["system"]
    saved = json.loads((meeting / summary.FILE).read_text(encoding="utf-8"))
    assert saved == written and saved["title"] == ANSWER["title"] and saved["language"] == "bn"


@pytest.mark.parametrize("stop, text, reason", [
    ("refusal", "", "declined"), ("max_tokens", "{", "too long"), ("end_turn", "not json", "wasn't a summary")])
def test_a_bad_answer_writes_nothing(meeting, stop, text, reason):
    with pytest.raises(summary.SummaryError, match=reason):
        summary.summarize(meeting, client=FakeClient(text, stop))
    assert not (meeting / summary.FILE).exists()


def test_needs_a_transcript_and_a_key(tmp_path, monkeypatch):
    with pytest.raises(summary.SummaryError, match="no transcript"):
        summary.summarize(tmp_path, client=FakeClient("{}"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(summary.SummaryError, match="Settings → AI summary"):
        summary.summarize(tmp_path)
