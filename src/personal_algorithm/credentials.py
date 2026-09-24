"""Credential-store boundary.

M1 defines the interface only. Provider tokens must never be stored in source
configuration or ordinary SQLite event tables.
"""

from __future__ import annotations

from typing import Protocol


class CredentialStore(Protocol):
    def put(self, provider_id: str, account_id: str, secret: str) -> str:
        """Store a secret and return an opaque credential reference."""
        ...

    def get(self, credential_ref: str) -> str:
        """Resolve an opaque reference for provider use."""
        ...

    def delete(self, credential_ref: str) -> None:
        """Destroy locally stored credential material."""
        ...
