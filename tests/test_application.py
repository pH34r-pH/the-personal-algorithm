from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from personal_algorithm.application import ApplicationSettings, create_private_app


class MemoryCredentials:
    def __init__(self):
        self.values = {}

    def put(self, provider_id, account_id, secret):
        ref = f"memory://{provider_id}/{account_id}"
        self.values[ref] = secret
        return ref

    def get(self, ref):
        return self.values[ref]

    def delete(self, ref):
        self.values.pop(ref, None)


def _github(request):
    if request.url.path == "/user":
        return httpx.Response(
            200,
            json={"id": 42, "login": "fixture", "html_url": "https://github.com/fixture"},
        )
    return httpx.Response(404)


def _oauth(request):
    return httpx.Response(200, json={"access_token": "token", "token_type": "bearer"})


def _client(tmp_path: Path) -> TestClient:
    settings = ApplicationSettings(
        database=str(tmp_path / "state.sqlite3"),
        owner_subject="owner",
        key_vault_url="https://unused.vault.azure.net",
        github_client_id="client-id",
        github_client_secret="client-secret",
    )
    return TestClient(
        create_private_app(
            settings,
            credentials=MemoryCredentials(),
            github_http=httpx.Client(
                base_url="https://api.github.com",
                transport=httpx.MockTransport(_github),
            ),
            oauth_http=httpx.Client(transport=httpx.MockTransport(_oauth)),
        )
    )


def test_health_is_public_but_private_api_requires_owner(tmp_path):
    client = _client(tmp_path)
    assert client.get("/health").status_code == 200
    assert client.get("/connections").status_code == 401


def test_owner_sees_shared_provider_registry_in_api_and_ui(tmp_path):
    client = _client(tmp_path)
    headers = {"x-ms-client-principal-name": "owner"}

    api = client.get("/connections", headers=headers)
    assert api.status_code == 200
    assert api.json()["providers"][0]["id"] == "github"

    ui = client.get("/app/connections", headers=headers)
    assert ui.status_code == 200
    assert "Connect GitHub" in ui.text


def test_settings_require_private_identity_and_provider_configuration(monkeypatch):
    for name in (
        "TPA_OWNER_SUBJECT",
        "TPA_KEY_VAULT_URL",
        "TPA_GITHUB_CLIENT_ID",
        "TPA_GITHUB_CLIENT_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)

    try:
        ApplicationSettings.from_env()
    except RuntimeError as exc:
        assert "owner_subject" in str(exc)
        assert "key_vault_url" in str(exc)
    else:
        raise AssertionError("missing private application settings were accepted")


def test_public_landing_describes_project_without_auth(tmp_path):
    client = _client(tmp_path)
    response = client.get("/")
    assert response.status_code == 200
    assert "The Personal" in response.text
    assert "Algorithm" in response.text
    assert "published feed" in response.text
    assert "Top 8" in response.text
    assert "pinned by the owner" in response.text
    assert "react" in response.text
    assert "share" in response.text
    assert "save" in response.text
    assert "publish" in response.text
    assert 'href="/app/"' in response.text


def test_private_app_redirects_unauthenticated_browser_to_entra(tmp_path):
    client = _client(tmp_path)
    response = client.get("/app/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == (
        "/.auth/login/aad?post_login_redirect_uri=%2Fapp%2F"
    )


def test_private_ui_redirect_preserves_requested_path(tmp_path):
    client = _client(tmp_path)
    response = client.get("/app/connections?from=home", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == (
        "/.auth/login/aad?post_login_redirect_uri=%2Fapp%2Fconnections%3Ffrom%3Dhome"
    )
