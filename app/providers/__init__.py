from __future__ import annotations
from ..core.config import settings


def get_provider():
    if settings.provider == "anthropic":
        from .anthropic_provider import AnthropicProvider
        return AnthropicProvider(settings.anthropic_model)
    from .mock import MockProvider
    return MockProvider([settings.samples_dir, settings.mock_truth_dir])
