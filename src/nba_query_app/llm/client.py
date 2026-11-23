"""What the rest of the app needs from an AI model, independent of the provider.

Everything that calls the model takes an `LlmClient`, so tests pass a fake one and
never call the real model (the free tier's limits are tight), and the provider or
model can change without touching the callers.
"""

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class Usage:
    """Tokens one call used (a token is roughly 3/4 of a word)."""

    input_tokens: int
    output_tokens: int
    thinking_tokens: int  # reasoning the model did before answering, billed as output

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.thinking_tokens + other.thinking_tokens,
        )


NO_USAGE = Usage(0, 0, 0)


@dataclass(frozen=True)
class LlmReply:
    text: str
    model: str
    usage: Usage
    seconds: float


class LlmError(Exception):
    """The model didn't give a usable reply. Messages never contain the API key."""


class LlmUnavailableError(LlmError):
    """The model is overloaded, timed out or unreachable. Worth retrying later."""


class LlmQuotaError(LlmError):
    """The free allowance (per minute or per day) is used up."""


class LlmClient(Protocol):
    def generate(
        self, *, system: str, prompt: str, json_schema: dict[str, Any] | None = None
    ) -> LlmReply:
        """One call. With json_schema, the reply text is JSON that follows it."""
        ...
