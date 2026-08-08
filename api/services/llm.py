# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Raunak Dey

"""LLM Provider abstraction for pluggable inference backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator


class LLMProvider(ABC):
    """Abstract base class for LLM inference providers."""

    @abstractmethod
    async def generate(self, prompt: str, **kwargs) -> str:
        """Generate a completion for the given prompt."""

    @abstractmethod
    async def stream_generate(self, prompt: str, **kwargs) -> AsyncIterator[str]:
        """Stream a completion for the given prompt."""

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Return the model identifier."""


class OllamaProvider(LLMProvider):
    """Ollama local inference provider."""

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "llama3.3:3b-instruct",
    ):
        self.base_url = base_url.rstrip("/")
        self._model = model

    @property
    def model_name(self) -> str:
        return self._model

    async def generate(self, prompt: str, **kwargs) -> str:
        import httpx

        url = f"{self.base_url}/api/generate"
        payload = {"model": self._model, "prompt": prompt, "stream": False}
        payload.update(kwargs)

        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            return response.json().get("response", "No answer generated.")

    async def stream_generate(self, prompt: str, **kwargs) -> AsyncIterator[str]:
        import json

        import httpx

        url = f"{self.base_url}/api/generate"
        payload = {"model": self._model, "prompt": prompt, "stream": True}
        payload.update(kwargs)

        async with (
            httpx.AsyncClient(timeout=120.0) as client,
            client.stream("POST", url, json=payload) as response,
        ):
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line:
                    try:
                        data = json.loads(line)
                        if "response" in data:
                            yield data["response"]
                    except json.JSONDecodeError, KeyError:
                        continue


class OpenAICompatibleProvider(LLMProvider):
    """OpenAI-compatible API provider (vLLM, OpenAI, etc.)."""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self._model = model
        self.api_key = api_key

    @property
    def model_name(self) -> str:
        return self._model

    async def generate(self, prompt: str, **kwargs) -> str:
        import httpx

        url = f"{self.base_url}/v1/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {"model": self._model, "prompt": prompt, "stream": False}
        payload.update(kwargs)

        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            return response.json()["choices"][0]["text"]

    async def stream_generate(self, prompt: str, **kwargs) -> AsyncIterator[str]:
        import json

        import httpx

        url = f"{self.base_url}/v1/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {"model": self._model, "prompt": prompt, "stream": True}
        payload.update(kwargs)

        async with (
            httpx.AsyncClient(timeout=120.0) as client,
            client.stream("POST", url, json=payload, headers=headers) as response,
        ):
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    data_str = line[6:]
                    if data_str.strip() == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                        if data["choices"][0]["text"]:
                            yield data["choices"][0]["text"]
                    except json.JSONDecodeError, KeyError:
                        continue


def get_llm_provider() -> LLMProvider:
    """Factory function to get the configured LLM provider."""
    import os

    provider_type = os.getenv("LLM_PROVIDER", "ollama").lower()

    if provider_type == "ollama":
        return OllamaProvider(
            base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            model=os.getenv("LLM_MODEL", "llama3.3:3b-instruct"),
        )
    elif provider_type in ("openai", "vllm", "openai-compatible"):
        return OpenAICompatibleProvider(
            base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com"),
            model=os.getenv("LLM_MODEL", "gpt-3.5-turbo"),
            api_key=os.getenv("OPENAI_API_KEY"),
        )
    else:
        raise ValueError(f"Unknown LLM provider: {provider_type}")
