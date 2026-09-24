"""Composition root for a configured single-owner Personal Algorithm instance."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI

from .azure_credentials import AzureKeyVaultCredentialStore
from .bootstrap import BootstrapCoordinator
from .connections import ConnectionStore
from .connections_api import create_connections_router
from .github_auth import GitHubAuthorization
from .oauth_state import DurableAuthorizationStateStore
from .github_provider import GitHubProvider
from .instance_auth import InstanceAuth
from .private_api import protect
from .provider_registry import ProviderRegistry
from .store import Store
from .ui import create_ui_router


@dataclass(frozen=True, slots=True)
class ApplicationSettings:
    database: str
    owner_subject: str
    key_vault_url: str
    github_client_id: str
    github_client_secret: str

    @classmethod
    def from_env(cls) -> ApplicationSettings:
        required = {
            "database": os.getenv("TPA_DATABASE", "data/personal-algorithm.sqlite3"),
            "owner_subject": os.getenv("TPA_OWNER_SUBJECT", ""),
            "key_vault_url": os.getenv("TPA_KEY_VAULT_URL", ""),
            "github_client_id": os.getenv("TPA_GITHUB_CLIENT_ID", ""),
            "github_client_secret": os.getenv("TPA_GITHUB_CLIENT_SECRET", ""),
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise RuntimeError(f"missing application settings: {', '.join(missing)}")
        return cls(**required)


def create_private_app(
    settings: ApplicationSettings,
    *,
    credentials=None,
    github_http=None,
    oauth_http=None,
) -> FastAPI:
    database_path = Path(settings.database)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    store = Store(database_path)
    connection_store = ConnectionStore(store)
    bootstrap = BootstrapCoordinator(store)

    credential_store = credentials or AzureKeyVaultCredentialStore(settings.key_vault_url)
    github = GitHubProvider(credential_store, client=github_http)
    registry = ProviderRegistry()
    registry.register(github)

    github_auth = GitHubAuthorization(
        client_id=settings.github_client_id,
        client_secret=settings.github_client_secret,
        credentials=credential_store,
        states=DurableAuthorizationStateStore(store),
        http=oauth_http,
        provider=github,
    )

    auth = InstanceAuth(owner_subject=settings.owner_subject)
    app = FastAPI(title="The Personal Algorithm", version="0.1.0")

    private_connections = protect(
        create_connections_router(
            registry=registry,
            connections=connection_store,
            bootstrap=bootstrap,
            github_auth=github_auth,
            github_provider=github,
        ),
        auth,
    )
    private_ui = protect(
        create_ui_router(registry=registry, connections=connection_store),
        auth,
    )

    app.include_router(private_connections)
    app.include_router(private_ui, prefix="/app")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


def app_from_env() -> FastAPI:
    return create_private_app(ApplicationSettings.from_env())
