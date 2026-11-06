"""Gemini rate limits, with the error bodies Gemini really sends (captured 2026-11-06)."""
import pytest
from google.genai import errors

from transcriber import gemini
from transcriber.base import QuotaExhausted


def quota_error(per: str = "day", value: str = "100", retry: str = "53956s") -> errors.APIError:
    quota_id = "GenerateRequestsPerDayPerProjectPerModel" if per == "day" else "GenerateRequestsPerMinutePerProjectPerModel"
    metric = f"generativelanguage.googleapis.com/generate_requests_per_model_per_{per}"
    return errors.APIError(429, {"error": {
        "code": 429, "status": "RESOURCE_EXHAUSTED",
        "message": f"You exceeded your current quota… limit: {value}, model: gemini-3.5-transcribe… Please retry in 14h59m16.2s.",
        "details": [
            {"@type": "type.googleapis.com/google.rpc.Help", "links": []},
            {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [
                {"quotaMetric": metric, "quotaId": quota_id, "quotaValue": value}]},
            {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry},
        ]}})


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.slept = 0.0

    def clock(self) -> float:
        return self.now

    def sleep(self, s: float) -> None:
        self.now += s
        self.slept += s


@pytest.fixture
def flash(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    t = gemini.GeminiFlashTranscriber("gemini-3.8-flash")
    fake = FakeClock()
    t.gate = gemini.RateGate(clock=fake.clock, sleep=fake.sleep)
    return t, fake


def test_a_daily_limit_is_recognised_with_its_reset_time():
    daily, wait, what = gemini.rate_limit(quota_error("day"))
    assert daily and wait == 53956 and what == "100 requests a day"


def test_a_per_minute_limit_is_waited_out():
    daily, wait, what = gemini.rate_limit(quota_error("minute", "10", "23s"))
    assert not daily and wait == 23 and what == "10 requests a minute"


def test_retry_time_is_read_from_the_message_when_details_are_missing():
    e = errors.APIError(429, {"error": {"code": 429, "message": "Please retry in 1h2m3s.", "status": "RESOURCE_EXHAUSTED"}})
    daily, wait, _ = gemini.rate_limit(e)
    assert wait == 3723 and daily  # longer than any per-minute wait


def test_daily_limit_stops_every_later_part_without_another_request(flash):
    t, _ = flash
    calls = []

    def request():
        calls.append(1)
        raise quota_error("day")
    with pytest.raises(QuotaExhausted, match="daily limit for gemini-3.8-flash is used up .100 requests a day.*resets in 14 h 59 min"):
        t._send(request)
    with pytest.raises(QuotaExhausted):
        t._send(request)
    assert len(calls) == 1  # the second part never reached Gemini


def test_per_minute_limit_holds_everyone_back_then_succeeds(flash):
    t, fake = flash
    answers = [quota_error("minute", "10", "23s"), "hello"]

    def request():
        a = answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a
    assert t._send(request) == "hello"
    assert fake.slept == pytest.approx(23)


def test_other_errors_are_not_swallowed(flash):
    t, _ = flash

    def request():
        raise errors.APIError(400, {"error": {"code": 400, "message": "bad audio", "status": "INVALID_ARGUMENT"}})
    with pytest.raises(errors.APIError, match="400"):
        t._send(request)


def test_a_stopped_run_does_not_sit_out_a_wait():
    import threading
    fake = FakeClock()
    gate = gemini.RateGate(clock=fake.clock, sleep=fake.sleep)
    gate.close_for(600)
    stop = threading.Event()
    stop.set()
    gate.wait(stop)
    assert fake.slept == 0
