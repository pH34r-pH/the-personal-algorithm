import pytest

from personal_algorithm.provider_registry import ProviderRegistry
from personal_algorithm.providers import (
    BootstrapCheckpoint,
    ProviderCapability,
    ProviderDescriptor,
)


class FakeProvider:
    descriptor = ProviderDescriptor(
        id="fake",
        display_name="Fake Provider",
        capabilities=frozenset(
            {
                ProviderCapability.SIGN_IN,
                ProviderCapability.HISTORY_IMPORT,
            }
        ),
        requested_scopes=("history:read",),
    )

    def bootstrap(self, connection, checkpoint):
        return BootstrapCheckpoint(
            cursor="done",
            imported=checkpoint.imported + 10,
        )


def test_registry_exposes_capabilities_without_credentials():
    registry = ProviderRegistry()
    registry.register(FakeProvider())

    descriptor = registry.descriptors()[0]
    assert descriptor.id == "fake"
    assert ProviderCapability.HISTORY_IMPORT in descriptor.capabilities
    assert not hasattr(descriptor, "credential_ref")


def test_registry_rejects_duplicate_provider_ids():
    registry = ProviderRegistry()
    registry.register(FakeProvider())
    with pytest.raises(ValueError):
        registry.register(FakeProvider())
