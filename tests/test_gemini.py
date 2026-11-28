"""The Gemini client. No network: the HTTP call is faked."""

import pytest
import requests

from nba_query_app.llm.client import LlmError, LlmQuotaError, LlmUnavailableError, Usage
from nba_query_app.llm.gemini import DEFAULT_MODEL, GeminiClient

KEY = "test-key-not-real"


class FakeResponse:
    def __init__(self, status_code: int, body: object) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> object:
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class FakePost:
    """Returns (or raises) the given outcome, recording the call."""

    def __init__(self, outcome: object) -> None:
        self.outcome = outcome
        self.calls: list[dict] = []

    def __call__(self, url: str, **kwargs: object) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def ok(text: str, finish: str = "STOP", **usage: int) -> FakeResponse:
    return FakeResponse(
        200,
        {
            "candidates": [
                {
                    "content": {
                        "parts": [{"text": "thinking...", "thought": True}, {"text": text}]
                    },
                    "finishReason": finish,
                }
            ],
            "usageMetadata": usage,
        },
    )


def client(outcome: object) -> tuple[GeminiClient, FakePost]:
    post = FakePost(outcome)
    return GeminiClient(KEY, post=post), post


def test_sends_the_key_in_a_header_and_asks_for_json() -> None:
    gemini, post = client(ok('{"a": 1}'))

    gemini.generate(system="be brief", prompt="hi", json_schema={"type": "object"})

    call = post.calls[0]
    assert call["url"].endswith(f"/models/{DEFAULT_MODEL}:generateContent")
    assert KEY not in call["url"]
    assert call["headers"] == {"x-goog-api-key": KEY}
    body = call["json"]
    assert body["systemInstruction"] == {"parts": [{"text": "be brief"}]}
    assert body["contents"] == [{"role": "user", "parts": [{"text": "hi"}]}]
    assert body["generationConfig"] == {
        "temperature": 0,
        "responseMimeType": "application/json",
        "responseSchema": {"type": "object"},
    }


def test_plain_text_request_has_no_schema() -> None:
    gemini, post = client(ok("hello"))

    gemini.generate(system="s", prompt="p")

    assert post.calls[0]["json"]["generationConfig"] == {"temperature": 0}


def test_reply_text_skips_thoughts_and_counts_tokens() -> None:
    gemini, _ = client(
        ok("answer", promptTokenCount=100, candidatesTokenCount=20, thoughtsTokenCount=5)
    )

    reply = gemini.generate(system="s", prompt="p")

    assert reply.text == "answer"
    assert reply.model == DEFAULT_MODEL
    assert reply.usage == Usage(input_tokens=100, output_tokens=20, thinking_tokens=5)


@pytest.mark.parametrize(
    ("outcome", "error"),
    [
        (FakeResponse(429, {"error": {"message": "quota exceeded"}}), LlmQuotaError),
        (FakeResponse(503, {"error": {"message": "high demand"}}), LlmUnavailableError),
        (FakeResponse(500, ValueError("not JSON")), LlmUnavailableError),
        (FakeResponse(400, {"error": {"message": "bad schema"}}), LlmError),
        (requests.Timeout("read timed out"), LlmUnavailableError),
        (requests.ConnectionError("no route"), LlmUnavailableError),
        (FakeResponse(200, {"promptFeedback": {"blockReason": "SAFETY"}}), LlmError),
        (ok('{"sql": "SELECT', finish="MAX_TOKENS"), LlmError),
        (ok("   "), LlmError),
    ],
)
def test_failures_raise_the_right_error_without_the_key(outcome: object, error: type) -> None:
    gemini, _ = client(outcome)

    with pytest.raises(error) as raised:
        gemini.generate(system="s", prompt="p")

    assert KEY not in str(raised.value)


def test_quota_and_overload_errors_are_llm_errors() -> None:
    assert issubclass(LlmQuotaError, LlmError)
    assert issubclass(LlmUnavailableError, LlmError)


def test_from_env_needs_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(LlmError, match="GEMINI_API_KEY"):
        GeminiClient.from_env()


def test_from_env_reads_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.5-flash-lite")

    assert GeminiClient.from_env().model == "gemini-3.5-flash-lite"


def test_usage_adds_up() -> None:
    assert Usage(1, 2, 3) + Usage(10, 20, 30) == Usage(11, 22, 33)
