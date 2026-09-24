"""Azure Key Vault implementation of the credential-store boundary."""

from __future__ import annotations

import hashlib
import re

from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient

_SAFE = re.compile(r"[^a-z0-9-]+")


def _secret_name(provider_id: str, account_id: str) -> str:
    """Build a stable Key Vault-compatible name without exposing raw account IDs."""
    provider = _SAFE.sub("-", provider_id.lower()).strip("-") or "provider"
    digest = hashlib.sha256(account_id.encode()).hexdigest()[:24]
    return f"tpa-{provider[:40]}-{digest}"


class AzureKeyVaultCredentialStore:
    """Store provider tokens as Key Vault secrets.

    The returned credential reference contains only the secret name. Vault
    location is deployment configuration and secret values never enter the
    application's ordinary persistence layer.
    """

    scheme = "akv"

    def __init__(self, vault_url: str, *, client: SecretClient | None = None) -> None:
        if not vault_url.startswith("https://") or ".vault.azure.net" not in vault_url:
            raise ValueError("vault_url must be an Azure Key Vault HTTPS URL")
        self.vault_url = vault_url.rstrip("/")
        self.client = client or SecretClient(
            vault_url=self.vault_url,
            credential=DefaultAzureCredential(),
        )

    def put(self, provider_id: str, account_id: str, secret: str) -> str:
        if not secret:
            raise ValueError("credential secret must not be empty")
        name = _secret_name(provider_id, account_id)
        self.client.set_secret(
            name,
            secret,
            tags={"application": "the-personal-algorithm", "provider": provider_id},
        )
        return f"{self.scheme}://{name}"

    def get(self, credential_ref: str) -> str:
        name = self._name_from_ref(credential_ref)
        result = self.client.get_secret(name)
        if result.value is None:
            raise KeyError(f"credential has no value: {credential_ref}")
        return result.value

    def delete(self, credential_ref: str) -> None:
        name = self._name_from_ref(credential_ref)
        # Begin deletion. Purge behavior remains a vault/deployment policy.
        self.client.begin_delete_secret(name).wait()

    def _name_from_ref(self, credential_ref: str) -> str:
        prefix = f"{self.scheme}://"
        if not credential_ref.startswith(prefix):
            raise ValueError("credential reference is not for Azure Key Vault")
        name = credential_ref.removeprefix(prefix)
        if not name or "/" in name:
            raise ValueError("invalid Azure Key Vault credential reference")
        return name
