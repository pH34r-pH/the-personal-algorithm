"""Provider registration independent of UI and credential storage."""

from __future__ import annotations

from .providers import Provider, ProviderDescriptor


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, Provider] = {}

    def register(self, provider: Provider) -> None:
        provider_id = provider.descriptor.id
        if provider_id in self._providers:
            raise ValueError(f"provider already registered: {provider_id}")
        self._providers[provider_id] = provider

    def descriptors(self) -> tuple[ProviderDescriptor, ...]:
        return tuple(
            provider.descriptor
            for _, provider in sorted(self._providers.items())
        )

    def get(self, provider_id: str) -> Provider:
        try:
            return self._providers[provider_id]
        except KeyError as exc:
            raise KeyError(f"unknown provider: {provider_id}") from exc
