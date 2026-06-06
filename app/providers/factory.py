from __future__ import annotations

from app.config import Settings
from app.providers.base import WritingProvider
from app.providers.mock_provider import MockWritingProvider


def build_provider(settings: Settings) -> WritingProvider:
    wants_openai = settings.provider_preference.lower() == "openai"
    if wants_openai and settings.openai_api_key:
        try:
            from app.providers.openai_provider import OpenAIWritingProvider

            return OpenAIWritingProvider(api_key=settings.openai_api_key, model=settings.openai_model)
        except Exception:
            # Fall back to deterministic local behavior if SDK/model setup is unavailable.
            return MockWritingProvider()
    return MockWritingProvider()

