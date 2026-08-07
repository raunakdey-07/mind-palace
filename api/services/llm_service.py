"""LLM service for pluggable inference backends."""

from __future__ import annotations

from typing import AsyncIterator, Optional

from api.services.llm import LLMProvider, get_llm_provider


class LLMService:
    """Service for LLM operations."""

    def __init__(self):
        self.provider: LLMProvider = get_llm_provider()

    async def generate(self, prompt: str, **kwargs) -> str:
        """Generate a completion."""
        return await self.provider.generate(prompt, **kwargs)

    async def stream_generate(self, prompt: str, **kwargs) -> AsyncIterator[str]:
        """Stream a completion."""
        async for chunk in self.provider.stream_generate(prompt, **kwargs):
            yield chunk

    @property
    def model_name(self) -> str:
        return self.provider.model_name
