"""
Microbe LLM — Provider registry for OpenAI-compatible LLM services.

Supports multiple providers (OpenAI, Groq, Ollama, etc.) through a unified
interface. Providers are auto-discovered from environment variables.
"""

import os
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from openai import AsyncOpenAI


class LLMResponse:
    """Unified response from any LLM provider."""

    def __init__(self, content: str, token_usage: Dict[str, int]):
        self.content = content
        self.token_usage = token_usage


class LLMProvider(ABC):
    """Base class for LLM providers."""

    @abstractmethod
    async def generate_completion(
        self,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 2000,
        response_format: Optional[Dict[str, str]] = None,
    ) -> LLMResponse:
        pass


class OpenAICompatibleProvider(LLMProvider):
    """
    Provider for any OpenAI-compatible API (OpenAI, Groq, Together, Ollama, etc.).
    """

    def __init__(self, api_key: str, base_url: Optional[str] = None):
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def generate_completion(
        self,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 2000,
        response_format: Optional[Dict[str, str]] = None,
    ) -> LLMResponse:
        response = await self.client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
        )

        token_usage = {
            "prompt": response.usage.prompt_tokens,
            "completion": response.usage.completion_tokens,
            "total": response.usage.total_tokens,
        }

        return LLMResponse(
            content=response.choices[0].message.content,
            token_usage=token_usage,
        )


class LLMProviderRegistry:
    """
    Auto-discovers and manages LLM providers from environment variables.
    """

    def __init__(self):
        self._providers: Dict[str, LLMProvider] = {}
        self._init_default_providers()

    def _init_default_providers(self):
        # OpenAI
        openai_key = os.getenv("OPENAI_API_KEY")
        if openai_key:
            self._providers["openai"] = OpenAICompatibleProvider(
                api_key=openai_key
            )

        # Groq
        groq_key = os.getenv("GROQ_API_KEY")
        if groq_key:
            self._providers["groq"] = OpenAICompatibleProvider(
                api_key=groq_key,
                base_url="https://api.groq.com/openai/v1",
            )

        # Together AI
        together_key = os.getenv("TOGETHER_API_KEY")
        if together_key:
            self._providers["together"] = OpenAICompatibleProvider(
                api_key=together_key,
                base_url="https://api.together.xyz/v1",
            )

    def register(
        self,
        name: str,
        provider: LLMProvider,
    ):
        """Register a custom provider."""
        self._providers[name] = provider

    def get_provider(
        self,
        model: str,
        requested_provider: Optional[str] = None,
    ) -> Optional[LLMProvider]:
        """
        Resolve the best provider for a given model.
        If a specific provider is requested, use it directly.
        Otherwise, use heuristics based on model name.
        """
        if requested_provider and requested_provider.lower() in self._providers:
            return self._providers[requested_provider.lower()]

        # Heuristics based on model name
        m = model.lower()
        if any(
            brand in m
            for brand in [
                "llama", "mixtral", "gemma", "qwen",
                "maverick", "scout", "kimi",
            ]
        ):
            if "groq" in self._providers:
                return self._providers["groq"]

        # Default to OpenAI
        return self._providers.get("openai")

    @property
    def available_providers(self) -> List[str]:
        """List all registered provider names."""
        return list(self._providers.keys())


# Global registry instance
provider_registry = LLMProviderRegistry()
