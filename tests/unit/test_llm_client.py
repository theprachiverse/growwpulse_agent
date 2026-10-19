"""Unit tests for LLMClient abstraction and retry/repair logic (Task 0.5)."""

import json

import httpx
import pytest
import respx
from pydantic import BaseModel, Field

from pulse.llm.client import (
    GroqLocalEmbedClient,
    MockLLMClient,
    get_llm_client,
)


class SampleOutput(BaseModel):
    category: str
    confidence: float = Field(ge=0.0, le=1.0)


def test_mock_llm_client() -> None:
    canned = SampleOutput(category="payments", confidence=0.95)
    client = MockLLMClient(canned_responses=[canned], embed_dim=64)

    res = client.complete_json("Classify review", SampleOutput)
    assert res.category == "payments"
    assert res.confidence == 0.95

    # Check embeddings
    embeddings = client.embed(["review one", "review two"])
    assert len(embeddings) == 2
    assert len(embeddings[0]) == 64

    # Check usage
    usage = client.get_usage()
    assert usage["llm_calls"] == 1
    assert usage["tokens_total"] > 0

    client.reset_usage()
    assert client.get_usage()["tokens_total"] == 0


@respx.mock
def test_groq_client_success() -> None:
    client = GroqLocalEmbedClient(
        api_key="test_gsk_key",
        chat_model="test-model",
        base_url="https://api.groq.com/openai/v1",
        timeout_s=5.0,
        max_retries=2,
    )

    mock_response = {
        "choices": [
            {
                "message": {
                    "content": json.dumps({"category": "kyc", "confidence": 0.88}),
                }
            }
        ],
        "usage": {"prompt_tokens": 50, "completion_tokens": 15},
    }

    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=mock_response)
    )

    result = client.complete_json("Classify this review", SampleOutput)
    assert result.category == "kyc"
    assert result.confidence == 0.88

    usage = client.get_usage()
    assert usage["tokens_prompt"] == 50
    assert usage["tokens_completion"] == 15


@respx.mock
def test_groq_client_retry_on_429() -> None:
    client = GroqLocalEmbedClient(
        api_key="test_gsk_key",
        base_url="https://api.groq.com/openai/v1",
        timeout_s=5.0,
        max_retries=3,
    )

    route = respx.post("https://api.groq.com/openai/v1/chat/completions")
    # First attempt: 429 rate limit
    route.side_effect = [
        httpx.Response(429, text="Rate limit exceeded"),
        httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps({"category": "trading", "confidence": 0.9}),
                        }
                    }
                ],
                "usage": {"prompt_tokens": 40, "completion_tokens": 10},
            },
        ),
    ]

    result = client.complete_json("Classify review", SampleOutput)
    assert result.category == "trading"
    assert route.call_count == 2


@respx.mock
def test_groq_client_repair_on_validation_error() -> None:
    client = GroqLocalEmbedClient(
        api_key="test_gsk_key",
        base_url="https://api.groq.com/openai/v1",
        timeout_s=5.0,
        max_retries=2,
    )

    route = respx.post("https://api.groq.com/openai/v1/chat/completions")
    # First response: invalid confidence (> 1.0)
    invalid_json = json.dumps({"category": "charges", "confidence": 5.0})
    # Second response (after repair re-prompt): fixed confidence (0.8)
    valid_json = json.dumps({"category": "charges", "confidence": 0.8})

    route.side_effect = [
        httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": invalid_json}}],
                "usage": {"prompt_tokens": 40, "completion_tokens": 10},
            },
        ),
        httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": valid_json}}],
                "usage": {"prompt_tokens": 70, "completion_tokens": 10},
            },
        ),
    ]

    result = client.complete_json("Classify review", SampleOutput)
    assert result.category == "charges"
    assert result.confidence == 0.8
    assert route.call_count == 2


def test_get_llm_client_factory() -> None:
    mock_client = get_llm_client(provider="mock")
    assert isinstance(mock_client, MockLLMClient)

    groq_client = get_llm_client(provider="groq", api_key="dummy_key")
    assert isinstance(groq_client, GroqLocalEmbedClient)

    with pytest.raises(ValueError):
        get_llm_client(provider="unknown_provider")


def test_server_retry_wait_parses_header_and_message() -> None:
    import httpx

    from pulse.llm.client import MAX_SERVER_WAIT_S, _server_retry_wait

    h = httpx.Response(429, headers={"retry-after": "12"}, text="x")
    assert _server_retry_wait(h) == 12.5
    m = httpx.Response(429, text="Rate limit. Please try again in 7.5s. Need more tokens?")
    assert _server_retry_wait(m) == 8.0
    ms = httpx.Response(429, text="try again in 850ms")
    assert 0.8 < _server_retry_wait(ms) < 1.5
    assert _server_retry_wait(httpx.Response(429, text="no hint")) == 0.0
    assert (
        _server_retry_wait(httpx.Response(429, headers={"retry-after": "9999"}, text=""))
        == MAX_SERVER_WAIT_S
    )
