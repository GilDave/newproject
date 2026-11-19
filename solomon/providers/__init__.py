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
from .antigravity import AntigravityProvider
from .ollama import OllamaProvider

# To add another AI: write providers/<name>.py (a CliProvider subclass) and add it here.
PROVIDER_CLASSES: dict[str, type[CliProvider]] = {
    "claude": ClaudeProvider,
    "codex": CodexProvider,
    "antigravity": AntigravityProvider,
    "ollama": OllamaProvider,  # local model over http://127.0.0.1:11434, read-only roles, never a default participant
}


def default_ais(providers: dict[str, Provider]) -> list[str]:
    """The AIs used when none is chosen: every installed one except helpers like the local model."""
    return [n for n, p in providers.items() if getattr(p, "default_participant", True) and getattr(p, "locate", lambda: True)()]


def build_providers(settings: Settings) -> dict[str, Provider]:
    providers: dict[str, Provider] = {}
    for name, cls in PROVIDER_CLASSES.items():
        if name == "ollama":
            providers[name] = cls(url=settings.ollama_url, model=settings.ollama_model, num_ctx=settings.ollama_num_ctx,
                                  keep_alive=settings.ollama_keep_alive)
            continue
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
    "default_ais",
]
