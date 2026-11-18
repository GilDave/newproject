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
from .gemini import GeminiProvider

# To add another AI: write providers/<name>.py (a CliProvider subclass) and add it here.
PROVIDER_CLASSES: dict[str, type[CliProvider]] = {
    "claude": ClaudeProvider,
    "codex": CodexProvider,
    "gemini": GeminiProvider,
}


def build_providers(settings: Settings) -> dict[str, Provider]:
    providers: dict[str, Provider] = {}
    for name, cls in PROVIDER_CLASSES.items():
        provider = cls(executable=settings.executables.get(name), extra_args=settings.extra_args.get(name))
        if name == "claude" and "claude_readonly" in settings.extra_args:
            provider.readonly_args = list(settings.extra_args["claude_readonly"])  # [] turns the default off
        if name == "claude" and "claude_write" in settings.extra_args:
            provider.write_args = list(settings.extra_args["claude_write"])
        providers[name] = provider
    return providers


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
