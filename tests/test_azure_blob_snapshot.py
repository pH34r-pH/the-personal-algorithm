"""Hosted snapshot configuration must not accept credentials in a URL."""

import pytest

import personal_algorithm.azure_blob_snapshot as module

IDENTITY = "11111111-2222-3333-4444-555555555555"
URL = "https://example123.blob.core.windows.net/tpa-state/snapshot.sqlite3"


def test_configured_client_uses_explicit_identity_and_closes_resources(monkeypatch):
    captured = {}
    closed = []

    class Credential:
        def __init__(self, **kwargs):
            captured["identity"] = kwargs

        def close(self):
            closed.append("credential")

    class Blob:
        def __init__(self, **kwargs):
            captured["blob"] = kwargs

        def close(self):
            closed.append("blob")

    monkeypatch.setattr(module, "ManagedIdentityCredential", Credential)
    monkeypatch.setattr(module, "BlobClient", Blob)
    session = module.configured_blob_snapshot(URL, IDENTITY)
    assert captured["identity"]["client_id"] == IDENTITY
    assert captured["blob"]["account_url"] == "https://example123.blob.core.windows.net"
    assert captured["blob"]["container_name"] == "tpa-state"
    assert captured["blob"]["blob_name"] == "snapshot.sqlite3"
    assert isinstance(captured["blob"]["credential"], Credential)
    for name in ("retry_total", "retry_connect", "retry_read", "retry_status"):
        assert captured["blob"][name] == 0
    assert captured["blob"]["connection_timeout"] == 5
    assert captured["blob"]["read_timeout"] == 10
    session.close()
    session.close()
    assert closed == ["blob", "credential"]


@pytest.mark.parametrize("url", [
    "http://example123.blob.core.windows.net/tpa-state/snapshot.sqlite3",
    URL + "?sig=secret",
    URL + "#fragment",
    "https://user@example123.blob.core.windows.net/tpa-state/snapshot.sqlite3",
    "https://example123.blob.core.windows.net:443/tpa-state/snapshot.sqlite3",
    "https://example123.blob.core.windows.net.evil.example/tpa-state/snapshot.sqlite3",
    "https://example123.blob.core.windows.net/tpa-state/nested/snapshot.sqlite3",
    "https://example123.blob.core.windows.net/tpa-state/%2e%2e",
    "https://example123.blob.core.windows.net/tpa--state/snapshot.sqlite3",
])
def test_rejects_unsafe_or_ambiguous_snapshot_urls(url):
    with pytest.raises(ValueError, match="invalid managed-identity"):
        module.configured_blob_snapshot(url, IDENTITY)


def test_invalid_identity_is_rejected_without_constructing_client(monkeypatch):
    def unexpected(**kwargs):
        pytest.fail("credential should not be constructed")
    monkeypatch.setattr(module, "ManagedIdentityCredential", unexpected)
    with pytest.raises(ValueError):
        module.configured_blob_snapshot(URL, "not-a-guid")


def test_failed_client_construction_closes_credential(monkeypatch):
    closed = []
    class Credential:
        def __init__(self, **kwargs): pass
        def close(self): closed.append(True)
    def fail(**kwargs):
        raise RuntimeError("client failed")
    monkeypatch.setattr(module, "ManagedIdentityCredential", Credential)
    monkeypatch.setattr(module, "BlobClient", fail)
    with pytest.raises(RuntimeError, match="client failed"):
        module.configured_blob_snapshot(URL, IDENTITY)
    assert closed == [True]
