from types import SimpleNamespace

import pytest

from personal_algorithm.azure_credentials import AzureKeyVaultCredentialStore


class FakeSecretClient:
    def __init__(self):
        self.values = {}
        self.deleted = []

    def set_secret(self, name, value, **kwargs):
        self.values[name] = value

    def get_secret(self, name):
        return SimpleNamespace(value=self.values.get(name))

    def begin_delete_secret(self, name):
        self.deleted.append(name)
        return SimpleNamespace(wait=lambda: None)


def test_key_vault_store_returns_opaque_reference_and_round_trips():
    client = FakeSecretClient()
    store = AzureKeyVaultCredentialStore(
        "https://fixture.vault.azure.net",
        client=client,
    )

    ref = store.put("github", "private-account@example.invalid", "token-value")
    assert ref.startswith("akv://tpa-github-")
    assert "private-account" not in ref
    assert store.get(ref) == "token-value"

    store.delete(ref)
    assert client.deleted


def test_account_identity_changes_secret_name_without_exposing_identity():
    client = FakeSecretClient()
    store = AzureKeyVaultCredentialStore(
        "https://fixture.vault.azure.net",
        client=client,
    )

    first = store.put("github", "account-a", "a")
    second = store.put("github", "account-b", "b")
    assert first != second
    assert "account-a" not in first
    assert "account-b" not in second


@pytest.mark.parametrize(
    "url",
    ["http://fixture.vault.azure.net", "https://example.com", "not-a-url"],
)
def test_rejects_non_key-vault_urls(url):
    with pytest.raises(ValueError):
        AzureKeyVaultCredentialStore(url, client=FakeSecretClient())
