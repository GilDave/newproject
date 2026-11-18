from __future__ import annotations

from ..settings import Settings
from .base import (
    CliProvider,
    Provider,
    ProviderError,
    ProviderResult,
    ProviderTimeout,
    ProviderUnavailable,
    QuotaExhausted,
)
from .claude import ClaudeProvider
from .codex import CodexProvider

# To add Gemini later: write providers/gemini.py (a CliProvider subclass) and add it here.
PROVIDER_CLASSES: dict[str, type[CliProvider]] = {
    "claude": ClaudeProvider,
    "codex": CodexProvider,
}


def build_providers(settings: Settings) -> dict[str, Provider]:
    return {
        name: cls(executable=settings.executables.get(name), extra_args=settings.extra_args.get(name))
        for name, cls in PROVIDER_CLASSES.items()
    }


__all__ = [
    "CliProvider",
    "Provider",
    "ProviderError",
    "ProviderResult",
    "ProviderTimeout",
    "ProviderUnavailable",
    "QuotaExhausted",
    "PROVIDER_CLASSES",
    "build_providers",
]
