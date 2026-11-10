from types import SimpleNamespace

import anthropic
import httpx
import openai
import pytest

from app.config import settings
from app.generation.llm_client import (
    AnthropicLLMClient,
    GenerationResult,
    LLMGenerationError,
    OpenAILLMClient,
)


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize("timeout", [False, True])
def test_provider_call_safety(monkeypatch, provider, timeout):
    sdk = openai if provider == "openai" else anthropic
    client_type = OpenAILLMClient if provider == "openai" else AnthropicLLMClient
    constructor = "OpenAI" if provider == "openai" else "Anthropic"
    monkeypatch.setattr(settings, f"{provider}_api_key", "fake-key")
    built = {}
    sent = {}
    error = sdk.APITimeoutError(request=httpx.Request("POST", "https://example.test"))
    result = GenerationResult(narrative="Test narrative")

    def parse(**kwargs):
        sent.update(kwargs)
        if timeout:
            raise error
        if provider == "openai":
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(parsed=result))],
                usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
            )
        return SimpleNamespace(
            stop_reason="end_turn", parsed_output=result,
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        )

    def build(**kwargs):
        built.update(kwargs)
        return SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(parse=parse)),
            messages=SimpleNamespace(parse=parse),
        )

    monkeypatch.setattr(sdk, constructor, build)
    client = client_type(model="test-model")
    assert built == {"api_key": "fake-key", "timeout": 60, "max_retries": 1}
    if timeout:
        with pytest.raises(LLMGenerationError) as caught:
            client.generate_narrative("system", "user")
        assert caught.value.__cause__ is error
    else:
        narrative, usage = client.generate_narrative("system", "user")
        assert narrative.narrative == "Test narrative"
        assert (usage.prompt_tokens, usage.completion_tokens) == (10, 5)
    assert sent["temperature"] == 0.2
    assert sent["model"] == "test-model"
