"""LLMClient interface + provider implementations.

Kept behind an interface (PRD Section 6.1/8: "swappable; do not hardcode a
vendor in business logic") so a different provider can be substituted without
touching app/generation/generate.py. get_llm_client() dispatches on
settings.llm_provider ("openai" | "anthropic").
"""

from typing import Protocol

from pydantic import BaseModel

from app.config import settings


class GenerationResult(BaseModel):
    narrative: str
    sources_used: list[str] = []


class TokenUsage(BaseModel):
    """Token counts + derived USD cost for a single LLM call (PRD Section 8:
    token/cost logging). ``cost_usd`` is None when the model's price is unknown."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float | None = None


# USD per 1M tokens (input, output). Kept small and explicit; unknown models
# fall back to cost_usd=None rather than guessing.
_PRICES: dict[str, tuple[float, float]] = {
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-5.6-luna": (0.20, 1.20),
    "claude-opus-4-8": (15.00, 75.00),
    "claude-3-5-sonnet-latest": (3.00, 15.00),
}


def _cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float | None:
    price = _PRICES.get(model)
    if price is None:
        return None
    in_price, out_price = price
    return round(prompt_tokens / 1e6 * in_price + completion_tokens / 1e6 * out_price, 6)


class LLMGenerationError(Exception):
    """Raised when the underlying LLM provider fails or is unreachable."""


class LLMClient(Protocol):
    def generate_narrative(
        self, system_prompt: str, user_prompt: str
    ) -> tuple[GenerationResult, TokenUsage]: ...


class OpenAILLMClient:
    """Default LLMClient implementation, backed by the OpenAI Chat Completions
    API with structured (Pydantic) parsing."""

    def __init__(self, model: str | None = None) -> None:
        import openai  # imported lazily so the package is optional until used

        if not settings.openai_api_key:
            raise LLMGenerationError(
                "OPENAI_API_KEY is not set. Add it to .env to enable narrative generation."
            )
        self._client = openai.OpenAI(api_key=settings.openai_api_key)
        self._model = model or settings.openai_model

    def generate_narrative(
        self, system_prompt: str, user_prompt: str
    ) -> tuple[GenerationResult, TokenUsage]:
        import openai

        try:
            completion = self._client.chat.completions.parse(
                model=self._model,
                max_completion_tokens=600,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format=GenerationResult,
            )
        except openai.OpenAIError as exc:
            raise LLMGenerationError(f"LLM call failed: {exc}") from exc

        parsed = completion.choices[0].message.parsed
        if parsed is None:
            raise LLMGenerationError("LLM returned no parseable output")
        u = completion.usage
        pt = getattr(u, "prompt_tokens", 0) or 0
        ct = getattr(u, "completion_tokens", 0) or 0
        usage = TokenUsage(prompt_tokens=pt, completion_tokens=ct, cost_usd=_cost_usd(self._model, pt, ct))
        return parsed, usage


class AnthropicLLMClient:
    """Alternate LLMClient implementation, backed by the Anthropic Messages API."""

    def __init__(self, model: str | None = None) -> None:
        import anthropic  # imported lazily so the package is optional until used

        if not settings.anthropic_api_key:
            raise LLMGenerationError(
                "ANTHROPIC_API_KEY is not set. Add it to .env to enable narrative generation."
            )
        self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self._model = model or settings.anthropic_model

    def generate_narrative(
        self, system_prompt: str, user_prompt: str
    ) -> tuple[GenerationResult, TokenUsage]:
        import anthropic

        try:
            response = self._client.messages.parse(
                model=self._model,
                max_tokens=600,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
                output_format=GenerationResult,
            )
        except anthropic.APIError as exc:
            raise LLMGenerationError(f"LLM call failed: {exc}") from exc

        if response.stop_reason == "refusal" or response.parsed_output is None:
            raise LLMGenerationError("LLM declined or returned no parseable output")
        u = getattr(response, "usage", None)
        pt = getattr(u, "input_tokens", 0) or 0
        ct = getattr(u, "output_tokens", 0) or 0
        usage = TokenUsage(prompt_tokens=pt, completion_tokens=ct, cost_usd=_cost_usd(self._model, pt, ct))
        return response.parsed_output, usage


def get_llm_client() -> LLMClient:
    if settings.llm_provider == "anthropic":
        return AnthropicLLMClient()
    return OpenAILLMClient()
