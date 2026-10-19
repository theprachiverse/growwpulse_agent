"""LLM client abstraction and implementations.

Implements:
- `LLMClient` Protocol (complete_json, embed, get_usage)
- `GroqLocalEmbedClient`: Groq for JSON chat completions + local sentence-transformers
- `MockLLMClient`: In-memory fixture/mock client for offline testing
- Factory `get_llm_client(...)`

Includes automatic retry with exponential backoff on transient errors and
a 1-shot repair re-prompt if Pydantic validation fails.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Protocol, TypeVar, runtime_checkable

import httpx
from pydantic import BaseModel, ValidationError

from pulse.observability.logging import get_logger

logger = get_logger("pulse.llm")

T = TypeVar("T", bound=BaseModel)


@runtime_checkable
class LLMClient(Protocol):
    """Protocol defining the LLM client interface."""

    def complete_json(
        self,
        prompt: str,
        schema: type[T],
        temperature: float = 0.0,
        system_prompt: str | None = None,
    ) -> T:
        """Completes a prompt and parses the response into the specified Pydantic schema."""
        ...

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Computes dense vector embeddings for a list of strings."""
        ...

    def get_usage(self) -> dict[str, int]:
        """Returns cumulative token counts, e.g. {'tokens_prompt': 120, 'tokens_completion': 50}."""
        ...

    def reset_usage(self) -> None:
        """Resets token usage counters to zero."""
        ...


MAX_SERVER_WAIT_S = 70.0  # one rate-limit window plus margin


def _server_retry_wait(resp: httpx.Response) -> float:
    """Seconds the server asked us to wait on a 429 (Retry-After header or 'try again in 12.3s')."""
    header = resp.headers.get("retry-after")
    if header:
        try:
            return min(float(header) + 0.5, MAX_SERVER_WAIT_S)
        except ValueError:
            pass
    m = re.search(r"try again in (?:(\d+)m)?\s*([\d.]+)(ms|s)", resp.text)
    if m:
        seconds = float(m.group(2)) / (1000 if m.group(3) == "ms" else 1)
        seconds += 60 * int(m.group(1) or 0)
        return min(seconds + 0.5, MAX_SERVER_WAIT_S)
    return 0.0


class GroqLocalEmbedClient:
    """Production client: Groq API for chat completion + sentence-transformers for embeddings."""

    def __init__(
        self,
        api_key: str | None = None,
        chat_model: str = "openai/gpt-oss-120b",
        embed_model: str = "sentence-transformers/all-MiniLM-L6-v2",
        base_url: str = "https://api.groq.com/openai/v1",
        timeout_s: float = 60.0,
        max_retries: int = 4,
    ) -> None:
        self.api_key = api_key or ""
        self.chat_model = chat_model
        self.embed_model_name = embed_model
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.max_retries = max_retries

        self._embedder: Any = None
        self._tokens_prompt: int = 0
        self._tokens_completion: int = 0
        self._calls: int = 0

    @property
    def embedder(self) -> Any:
        """Lazy loader for sentence-transformers model."""
        if self._embedder is None:
            from sentence_transformers import SentenceTransformer

            logger.info("loading_embedding_model", model=self.embed_model_name)
            self._embedder = SentenceTransformer(self.embed_model_name)
        return self._embedder

    def get_usage(self) -> dict[str, int]:
        return {
            "tokens_prompt": self._tokens_prompt,
            "tokens_completion": self._tokens_completion,
            "tokens_total": self._tokens_prompt + self._tokens_completion,
            "llm_calls": self._calls,
        }

    def reset_usage(self) -> None:
        self._tokens_prompt = 0
        self._tokens_completion = 0
        self._calls = 0

    def _call_groq(
        self,
        messages: list[dict[str, str]],
        temperature: float,
    ) -> tuple[str, dict[str, int]]:
        """Makes an HTTP call to Groq /chat/completions with retry/backoff."""
        if not self.api_key:
            raise ValueError(
                "LLM_API_KEY is not set. Please add it to your .env file or environment."
            )

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.chat_model,
            "messages": messages,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        }

        last_exc: Exception | None = None
        delay = 1.0

        for attempt in range(1, self.max_retries + 1):
            try:
                with httpx.Client(timeout=self.timeout_s) as client:
                    resp = client.post(url, headers=headers, json=payload)

                if resp.status_code == 200:
                    data = resp.json()
                    content = data["choices"][0]["message"]["content"]
                    usage = data.get("usage", {})
                    token_info = {
                        "prompt_tokens": usage.get("prompt_tokens", 0),
                        "completion_tokens": usage.get("completion_tokens", 0),
                    }
                    return content, token_info

                # Retry on 429 rate limit or 5xx server errors
                if resp.status_code in (429, 500, 502, 503, 504):
                    wait = (
                        max(delay, _server_retry_wait(resp)) if resp.status_code == 429 else delay
                    )
                    last_exc = RuntimeError(f"HTTP {resp.status_code}: {resp.text[:120]}")
                    logger.warning(
                        "llm_transient_error",
                        status_code=resp.status_code,
                        attempt=attempt,
                        delay=wait,
                        error=resp.text[:200],
                    )
                    time.sleep(wait)
                    delay *= 2
                    continue

                resp.raise_for_status()

            except (httpx.RequestError, httpx.HTTPStatusError) as exc:
                last_exc = exc
                logger.warning(
                    "llm_request_failed",
                    attempt=attempt,
                    delay=delay,
                    error=str(exc),
                )
                time.sleep(delay)
                delay *= 2

        raise RuntimeError(
            f"Failed to complete LLM request after {self.max_retries} attempts: {last_exc}"
        )

    def complete_json(
        self,
        prompt: str,
        schema: type[T],
        temperature: float = 0.0,
        system_prompt: str | None = None,
    ) -> T:
        """Completes prompt, parses into `schema`, retrying once on parse failure."""
        schema_json = json.dumps(schema.model_json_schema(), indent=2)
        base_system = (
            "You are a helpful structured JSON assistant. Output valid JSON that strictly adheres "
            f"to the following JSON Schema:\n{schema_json}"
        )
        if system_prompt:
            base_system = f"{system_prompt}\n\n{base_system}"

        messages = [
            {"role": "system", "content": base_system},
            {"role": "user", "content": prompt},
        ]

        raw_text, usage = self._call_groq(messages, temperature=temperature)
        self._tokens_prompt += usage.get("prompt_tokens", 0)
        self._tokens_completion += usage.get("completion_tokens", 0)
        self._calls += 1

        try:
            return schema.model_validate_json(raw_text)
        except (ValidationError, json.JSONDecodeError) as parse_err:
            logger.warning(
                "llm_schema_validation_failed_retrying",
                error=str(parse_err),
                raw_preview=raw_text[:200],
            )
            # Re-prompt once with the error
            repair_messages = list(messages)
            repair_messages.append({"role": "assistant", "content": raw_text})
            repair_messages.append(
                {
                    "role": "user",
                    "content": (
                        f"Your output failed validation with error:\n{parse_err}\n"
                        "Please fix the error and output valid JSON strictly matching the schema."
                    ),
                }
            )

            raw_text_2, usage_2 = self._call_groq(repair_messages, temperature=temperature)
            self._tokens_prompt += usage_2.get("prompt_tokens", 0)
            self._tokens_completion += usage_2.get("completion_tokens", 0)
            self._calls += 1

            return schema.model_validate_json(raw_text_2)

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        embeddings = self.embedder.encode(
            texts,
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        return [[float(x) for x in emb] for emb in embeddings]


class MockLLMClient:
    """Mock LLM client for deterministic tests and offline development."""

    def __init__(
        self,
        canned_responses: list[Any] | None = None,
        embed_dim: int = 384,
    ) -> None:
        self.canned_responses = list(canned_responses or [])
        self.embed_dim = embed_dim
        self._tokens_prompt = 0
        self._tokens_completion = 0
        self._calls = 0

    def get_usage(self) -> dict[str, int]:
        return {
            "tokens_prompt": self._tokens_prompt,
            "tokens_completion": self._tokens_completion,
            "tokens_total": self._tokens_prompt + self._tokens_completion,
            "llm_calls": self._calls,
        }

    def reset_usage(self) -> None:
        self._tokens_prompt = 0
        self._tokens_completion = 0
        self._calls = 0

    def complete_json(
        self,
        prompt: str,
        schema: type[T],
        temperature: float = 0.0,
        system_prompt: str | None = None,
    ) -> T:
        self._calls += 1
        self._tokens_prompt += len(prompt) // 4
        self._tokens_completion += 50

        if self.canned_responses:
            item = self.canned_responses.pop(0)
            if isinstance(item, schema):
                return item
            if isinstance(item, str):
                return schema.model_validate_json(item)
            if isinstance(item, dict):
                return schema.model_validate(item)

        # Fallback: construct dummy instance using schema default fields if possible
        try:
            return schema.model_validate({})
        except ValidationError as err:
            raise RuntimeError(
                f"MockLLMClient has no canned response available for schema {schema.__name__}"
            ) from err

    def embed(self, texts: list[str]) -> list[list[float]]:
        # Deterministic pseudo-embedding based on string length and hash
        result: list[list[float]] = []
        for text in texts:
            seed = hash(text) % 10000
            vector = [((seed + i) % 100) / 100.0 for i in range(self.embed_dim)]
            result.append(vector)
        return result


def get_llm_client(
    provider: str | None = None,
    api_key: str | None = None,
    chat_model: str | None = None,
    embed_model: str | None = None,
) -> LLMClient:
    """Factory creating an LLMClient instance based on configuration."""
    from pulse.config import get_settings

    settings = get_settings()
    prov = (provider or settings.llm_provider).lower()
    key = api_key or settings.llm_api_key
    c_model = chat_model or settings.app_config.llm.model_chat
    e_model = embed_model or settings.app_config.llm.model_embed

    if prov in ("groq", "openai"):
        return GroqLocalEmbedClient(
            api_key=key,
            chat_model=c_model,
            embed_model=e_model,
            base_url=settings.app_config.llm.base_url,
            timeout_s=float(settings.app_config.llm.timeout_s),
            max_retries=settings.app_config.llm.max_retries,
        )
    elif prov == "mock":
        return MockLLMClient()
    else:
        raise ValueError(f"Unsupported LLM_PROVIDER '{prov}'. Supported in P0: 'groq' (or 'mock').")
