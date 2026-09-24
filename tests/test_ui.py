from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from personal_algorithm.connections import ConnectionStore
from personal_algorithm.instance_auth import InstanceAuth
from personal_algorithm.private_api import protect
from personal_algorithm.provider_registry import ProviderRegistry
from personal_algorithm.providers import Connection, ConnectionStatus, ProviderCapability, ProviderDescriptor
from personal_algorithm.store import Store
from personal_algorithm.ui import create_ui_router


class GitHubFixture:
    descriptor = ProviderDescriptor(
        id="github", display_name="GitHub",
        capabilities=frozenset({ProviderCapability.SIGN_IN, ProviderCapability.HISTORY_IMPORT, ProviderCapability.CONTINUOUS_SYNC}),
        requested_scopes=("read:user",),
    )


def _client(connected=False):
    store = Store()
    connections = ConnectionStore(store)
    if connected:
        connections.put(Connection(
            provider_id="github", account_label="fixture", status=ConnectionStatus.CONNECTED,
            granted_scopes=("read:user",), connected_at=datetime(2026, 9, 23, tzinfo=UTC),
            credential_ref="akv://opaque",
        ))
    registry = ProviderRegistry()
    registry.register(GitHubFixture())
    app = FastAPI()
    app.include_router(protect(
        create_ui_router(registry=registry, connections=connections),
        InstanceAuth(owner_subject="owner"),
    ), prefix="/app")
    return TestClient(app)


def test_ui_is_private():
    assert _client().get("/app/connections").status_code == 401


def test_connections_page_offers_github_connect():
    response = _client().get("/app/connections", headers={"x-personal-algorithm-subject": "owner"})
    assert response.status_code == 200
    assert "Connect GitHub" in response.text
    assert "Bootstrap history" not in response.text


def test_connected_account_offers_explicit_bootstrap():
    response = _client(connected=True).get(
        "/app/connections", headers={"x-personal-algorithm-subject": "owner"}
    )
    assert "Connected as fixture" in response.text
    assert "Bootstrap history" in response.text
    assert "continuous" in response.text
    assert "separate choices" in response.text
