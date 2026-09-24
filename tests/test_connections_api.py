from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from personal_algorithm.bootstrap import BootstrapCoordinator
from personal_algorithm.connections import ConnectionStore
from personal_algorithm.connections_api import create_connections_router
from personal_algorithm.models import Candidate
from personal_algorithm.provider_registry import ProviderRegistry
from personal_algorithm.providers import (
    BootstrapCheckpoint,
    Connection,
    ConnectionStatus,
    ProviderCapability,
    ProviderDescriptor,
)
from personal_algorithm.store import Store


class Provider:
    descriptor = ProviderDescriptor(
        id="fixture",
        display_name="Fixture",
        capabilities=frozenset(
            {ProviderCapability.HISTORY_IMPORT, ProviderCapability.CONTINUOUS_SYNC}
        ),
        requested_scopes=("read",),
    )

    def bootstrap(self, connection, checkpoint):
        return BootstrapCheckpoint(
            cursor=None,
            imported=checkpoint.imported + 1,
            metadata={"repositories": [{"id": 1}]},
        )

    def repository_candidates(self, repositories):
        return (
            Candidate(
                id="fixture:1",
                source="fixture",
                source_id="1",
                canonical_url="https://example.invalid/1",
                title="Fixture",
                discovered_at=datetime(2026, 9, 23, tzinfo=UTC),
                content_type="repository",
            ),
        )


def _client():
    store = Store()
    connections = ConnectionStore(store)
    connections.put(
        Connection(
            provider_id="fixture",
            account_label="me",
            status=ConnectionStatus.CONNECTED,
            granted_scopes=("read",),
            connected_at=datetime(2026, 9, 23, tzinfo=UTC),
            credential_ref="memory://fixture/me",
        )
    )
    registry = ProviderRegistry()
    registry.register(Provider())
    bootstrap = BootstrapCoordinator(store)
    app = FastAPI()
    app.include_router(
        create_connections_router(
            registry=registry,
            connections=connections,
            bootstrap=bootstrap,
        )
    )
    return TestClient(app), store


def test_connections_surface_capabilities_and_status():
    client, _ = _client()
    body = client.get("/connections").json()
    provider = body["providers"][0]
    assert provider["id"] == "fixture"
    assert provider["connected"] is True
    assert provider["account_label"] == "me"
    assert "history_import" in provider["capabilities"]


def test_manual_bootstrap_progress_is_visible():
    client, store = _client()
    started = client.post("/connections/fixture/bootstrap")
    assert started.status_code == 201
    job = started.json()
    assert job["status"] == "pending"

    stepped = client.post(f"/connections/fixture/bootstrap/{job['id']}/step")
    assert stepped.status_code == 200
    assert stepped.json()["status"] == "complete"
    assert stepped.json()["checkpoint"]["imported"] == 1
    assert stepped.json()["candidates_imported_this_step"] == 1

    status = client.get(f"/connections/fixture/bootstrap/{job['id']}")
    assert status.json()["status"] == "complete"
    assert store.connection.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 1
