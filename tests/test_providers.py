from datetime import UTC, datetime

import pytest

from personal_algorithm.provider_registry import ProviderRegistry
from personal_algorithm.providers import (
    BootstrapCheckpoint,
    Connection,
    ConnectionStatus,
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


def test_provider_bootstrap_uses_opaque_connection_reference():
    provider = FakeProvider()
    connection = Connection(
        provider_id="fake",
        account_label="fixture",
        status=ConnectionStatus.CONNECTED,
        granted_scopes=("history:read",),
        connected_at=datetime(2026, 9, 23, tzinfo=UTC),
        credential_ref="secret://opaque/123",
    )

    checkpoint = provider.bootstrap(connection, BootstrapCheckpoint())
    assert checkpoint.imported == 10
    assert checkpoint.cursor == "done"


def test_registry_rejects_duplicate_provider_ids():
    registry = ProviderRegistry()
    registry.register(FakeProvider())
    with pytest.raises(ValueError):
        registry.register(FakeProvider())
