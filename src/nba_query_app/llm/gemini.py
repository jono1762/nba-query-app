"""Google Gemini over its REST API (no SDK, to keep the deployed package small).

The model is a setting (GEMINI_MODEL), defaulting to the cheapest one that did the
job in testing (see spikes/FINDINGS.md). The API key is read from GEMINI_API_KEY and
sent in a header, never in the URL, so it can't end up in logs or error messages.
"""

import os
import time
from collections.abc import Callable
from typing import Any

import requests

from nba_query_app.llm.client import (
    LlmError,
    LlmQuotaError,
    LlmReply,
    LlmUnavailableError,
    Usage,
)

API = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODEL = "gemini-3.1-flash-lite"
# API Gateway cuts a request off at 29 s and a question takes two calls; the Lite
# model usually replies in 2-8 s.
DEFAULT_TIMEOUT_SECONDS = 20.0


class GeminiClient:
    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        post: Callable[..., requests.Response] = requests.post,
    ) -> None:
        if not api_key:
            raise ValueError("A Gemini API key is required")
        self._api_key = api_key
        self.model = model
        self._timeout = timeout
        self._post = post

    @classmethod
    def from_env(cls) -> "GeminiClient":
        """GEMINI_API_KEY (required) and GEMINI_MODEL (optional) from the environment."""
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise LlmError("GEMINI_API_KEY is not set")
        return cls(api_key, model=os.environ.get("GEMINI_MODEL") or DEFAULT_MODEL)

    def generate(
        self, *, system: str, prompt: str, json_schema: dict[str, Any] | None = None
    ) -> LlmReply:
        config: dict[str, Any] = {"temperature": 0}
        if json_schema is not None:
            # "Structured output": the reply must be JSON following this schema.
            config["responseMimeType"] = "application/json"
            config["responseSchema"] = json_schema
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": config,
        }
        started = time.perf_counter()
        try:
            response = self._post(
                f"{API}/models/{self.model}:generateContent",
                headers={"x-goog-api-key": self._api_key},
                json=body,
                timeout=self._timeout,
            )
        except requests.Timeout as error:
            raise LlmUnavailableError(
                f"{self.model} didn't reply within {self._timeout} s"
            ) from error
        except requests.RequestException as error:
            raise LlmUnavailableError(f"Couldn't reach Gemini: {type(error).__name__}") from error
        seconds = time.perf_counter() - started
        data = _json_or_empty(response)

        if response.status_code != 200:
            message = f"{self.model}: HTTP {response.status_code}: {_error_message(data)}"
            if response.status_code == 429:
                raise LlmQuotaError(message)
            if response.status_code >= 500:
                raise LlmUnavailableError(message)
            raise LlmError(message)
        return LlmReply(_reply_text(data, self.model), self.model, _usage(data), seconds)


def _json_or_empty(response: requests.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _error_message(data: dict[str, Any]) -> str:
    error = data.get("error")
    message = error.get("message") if isinstance(error, dict) else None
    return str(message)[:300] if message else "no details"


def _reply_text(data: dict[str, Any], model: str) -> str:
    """The answer text, without any "thought" parts. Refuses blocked or empty replies."""
    candidates = data.get("candidates") or []
    if not candidates:
        reason = (data.get("promptFeedback") or {}).get("blockReason", "no reply")
        raise LlmError(f"{model} gave no reply ({reason})")
    candidate = candidates[0]
    parts = (candidate.get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    if not text.strip():
        raise LlmError(f"{model} gave an empty reply ({candidate.get('finishReason', 'unknown')})")
    if candidate.get("finishReason") not in (None, "STOP"):
        # e.g. MAX_TOKENS: the reply was cut off, so JSON in it would be incomplete.
        raise LlmError(f"{model} stopped early ({candidate['finishReason']})")
    return text


def _usage(data: dict[str, Any]) -> Usage:
    meta = data.get("usageMetadata") or {}
    return Usage(
        input_tokens=meta.get("promptTokenCount", 0),
        output_tokens=meta.get("candidatesTokenCount", 0),
        thinking_tokens=meta.get("thoughtsTokenCount", 0),
    )
