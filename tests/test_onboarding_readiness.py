import io
import sqlite3
import zipfile

import pytest
from fastapi.testclient import TestClient

from personal_algorithm.application import ApplicationSettings, create_private_app

OWNER = {"x-ms-client-principal-id": "owner"}
CSV = b'id,permalink,date,body\nabc,/r/example/comments/p/title/abc/,2026-10-01T12:00:00Z,Example comment\n'


def _settings(tmp_path, **overrides):
    return ApplicationSettings(
        database=str(tmp_path / "state.sqlite3"),
        owner_object_id="owner",
        archive_dir=str(tmp_path / "archives"),
        **overrides,
    )


def _upload(client, content=CSV, filename="comments.csv", hint=None):
    headers = {**OWNER, "x-archive-filename": filename}
    if hint:
        headers["x-provider-hint"] = hint
    response = client.post("/archives/upload", content=content, headers=headers)
    assert response.status_code == 201
    return response.json()


@pytest.mark.parametrize("provider_settings", [
    {}, {"github_client_id": "incomplete"},
    {"github_client_id": "client", "github_client_secret": "secret"},
    {"key_vault_url": "https://unused.vault.azure.net"},
])
def test_archive_only_startup_without_provider_io(tmp_path, monkeypatch, provider_settings):
    def forbidden(*args, **kwargs):
        raise AssertionError("archive-only startup must not initialize a credential client")
    monkeypatch.setattr("personal_algorithm.application.AzureKeyVaultCredentialStore", forbidden)
    client = TestClient(create_private_app(_settings(tmp_path, **provider_settings)))
    assert client.get("/health").status_code == 200
    assert client.get("/archives").status_code == 401
    assert client.get("/archives", headers={"x-ms-client-principal-id": "other"}).status_code == 403
    assert client.get("/connections", headers=OWNER).json() == {"providers": []}
    assert client.get("/connections/github/connect", headers=OWNER).status_code == 503
    page = client.get("/app/onboarding", headers=OWNER)
    assert page.status_code == 200
    assert "Operator setup required" in page.text
    assert 'href="/connections/github/connect"' not in page.text
    assert "Store selected files" in page.text


def test_readiness_is_private_and_does_not_claim_live_access(tmp_path, monkeypatch):
    monkeypatch.setenv("TPA_SOURCE_REVISION", "fixture-source")
    monkeypatch.setenv("CONTAINER_APP_REVISION", "fixture-deployment")
    monkeypatch.setenv("TPA_GITHUB_CLIENT_SECRET", "not-for-diagnostics")
    client = TestClient(create_private_app(_settings(tmp_path)))
    assert client.get("/app/onboarding/readiness", follow_redirects=False).status_code == 307
    response = client.get("/app/onboarding/readiness", headers=OWNER)
    value = response.json()
    assert value["scope"] == "configuration_only"
    assert value["live_checks"] == "not_verified"
    assert value["source_revision"] == "fixture-source"
    assert value["deployment_revision"] == "fixture-deployment"
    assert value["durable_worker"] == "not_implemented"
    providers = {item["id"]: item for item in value["providers"]}
    assert providers["github"]["oauth"] == "operator_setup_required"
    assert providers["google"]["archive_parser"] == "not_implemented"
    assert providers["reddit"]["archive_parser"] == "implemented"
    assert "not-for-diagnostics" not in response.text


def test_reddit_upload_import_repeat_and_restart(tmp_path):
    settings = _settings(tmp_path)
    client = TestClient(create_private_app(settings))
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as archive:
        archive.writestr("export/comments.csv", CSV)
        archive.writestr("messages.csv", "not imported by this parser")
    stored = _upload(client, content.getvalue(), "reddit-export.zip")
    assert stored["detected_provider"] == "reddit"
    assert stored["import_available"] is True
    path = f'/archives/{stored["sha256"]}/import'
    assert client.post(path).status_code == 401
    for _ in range(2):
        imported = client.post(path, headers=OWNER)
        assert imported.status_code == 200
        assert imported.json()["summary"]["scope"] == "posts_and_comments_only"
        assert imported.json()["summary"]["events_written"] == 1
    with sqlite3.connect(settings.database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM personal_events").fetchone()[0] == 1
    restarted = TestClient(create_private_app(settings))
    retained, = restarted.get("/archives", headers=OWNER).json()["archives"]
    assert retained["status"] == "imported"
    assert retained["sha256"] == stored["sha256"]
    page = restarted.get("/app/onboarding", headers=OWNER).text
    assert "Posts and comments CSVs only" in page
    assert "Reddit inbox" in page


def test_failed_parse_retains_archive_and_reports_failure(tmp_path):
    client = TestClient(create_private_app(_settings(tmp_path)))
    stored = _upload(client, b"wrong,headers\nprivate-fixture,content\n")
    response = client.post(f'/archives/{stored["sha256"]}/import', headers=OWNER)
    assert response.status_code == 422
    assert "private-fixture" not in response.text
    retained, = client.get("/archives", headers=OWNER).json()["archives"]
    assert retained["status"] == "failed"
    assert retained["sha256"] == stored["sha256"]
    assert list((tmp_path / "archives").rglob("comments.csv"))


def test_unsupported_archive_is_not_presented_as_imported(tmp_path):
    client = TestClient(create_private_app(_settings(tmp_path)))
    stored = _upload(client, b"{}", "google-export.json", "google")
    assert stored["import_available"] is False
    response = client.post(f'/archives/{stored["sha256"]}/import', headers=OWNER)
    assert response.status_code == 409
    retained, = client.get("/archives", headers=OWNER).json()["archives"]
    assert retained["status"] == "stored"
    assert "Retained only; parser not implemented" in retained["import_coverage"]
