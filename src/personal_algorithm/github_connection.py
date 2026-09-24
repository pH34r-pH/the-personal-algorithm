"""GitHub connection finalization independent of the web OAuth callback."""

from __future__ import annotations

from datetime import UTC, datetime

from .credentials import CredentialStore
from .github_provider import GitHubProvider
from .providers import Connection, ConnectionStatus


def connect_github(
    access_token: str,
    *,
    credentials: CredentialStore,
    provider: GitHubProvider | None = None,
) -> Connection:
    """Validate a GitHub token, store it securely, and return an opaque Connection."""
    if not access_token:
        raise ValueError("GitHub access token must not be empty")

    validating = provider or GitHubProvider(credentials)
    identity = validating.identity_with_token(access_token)
    credential_ref = credentials.put("github", identity["id"], access_token)
    return Connection(
        provider_id="github",
        account_label=identity["login"],
        status=ConnectionStatus.CONNECTED,
        granted_scopes=("read:user",),
        connected_at=datetime.now(UTC),
        credential_ref=credential_ref,
        metadata={"github_user_id": identity["id"], "html_url": identity["html_url"]},
    )
