import sqlite3
from contextlib import closing
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from personal_algorithm.application import ApplicationSettings, app_from_env, create_private_app

ACCESS_TOKEN = "synthetic-access-fixture"
REFRESH_TOKEN = "synthetic-refresh-fixture"
PROVIDER_SESSION = "synthetic-session-fixture"
CLIENT_SECRET = "synthetic-client-secret-fixture"
UNUSED_PASSWORD = "synthetic-unpersisted-password"
UNUSED_PRIVATE_FIELD = "synthetic-unpersisted@example.invalid"
OAUTH_CODE = "synthetic-oauth-code"
OWNER = {"x-ms-client-principal-id": "owner-id"}


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
            json={"id": 42, "login": "fixture", "html_url": "https://github.com/fixture",
                  "email": UNUSED_PRIVATE_FIELD},
        )
    return httpx.Response(404)


def _oauth(request):
    return httpx.Response(200, json={"access_token": ACCESS_TOKEN, "token_type": "bearer",
                                     "refresh_token": REFRESH_TOKEN, "id_token": PROVIDER_SESSION,
                                     "password": UNUSED_PASSWORD})


def _client(tmp_path: Path, *, credentials=None, oauth_transport=_oauth, snapshot=None) -> TestClient:
    settings = ApplicationSettings(
        database=str(tmp_path / "state.sqlite3"),
        owner_object_id="owner-id",
        key_vault_url="https://unused.vault.azure.net",
        github_client_id="client-id",
        github_client_secret=CLIENT_SECRET,
        archive_dir=str(tmp_path / "archives"),
        database_snapshot=snapshot,
    )
    return TestClient(
        create_private_app(
            settings,
            credentials=credentials if credentials is not None else MemoryCredentials(),
            github_http=httpx.Client(
                base_url="https://api.github.com",
                transport=httpx.MockTransport(_github),
            ),
            oauth_http=httpx.Client(transport=httpx.MockTransport(oauth_transport)),
        ),
        base_url="https://testserver",
    )


def test_health_is_public_but_private_api_requires_owner(tmp_path):
    client = _client(tmp_path)
    assert client.get("/health").status_code == 200
    assert client.get("/connections").status_code == 401


def test_owner_sees_shared_provider_registry_in_api_and_ui(tmp_path):
    client = _client(tmp_path)
    headers = {"x-ms-client-principal-id": "owner-id"}

    api = client.get("/connections", headers=headers)
    assert api.status_code == 200
    assert api.json()["providers"][0]["id"] == "github"

    ui = client.get("/app/connections", headers=headers)
    assert ui.status_code == 200
    assert "Connect GitHub" in ui.text


def test_settings_require_private_identity_but_not_optional_providers(monkeypatch):
    for name in (
        "TPA_OWNER_OBJECT_ID",
        "TPA_KEY_VAULT_URL",
        "TPA_GITHUB_CLIENT_ID",
        "TPA_GITHUB_CLIENT_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)

    try:
        ApplicationSettings.from_env()
    except RuntimeError as exc:
        assert "owner_object_id" in str(exc)
        assert "key_vault_url" not in str(exc)
    else:
        raise AssertionError("missing private application settings were accepted")

    monkeypatch.setenv("TPA_OWNER_OBJECT_ID", "owner-id")
    settings = ApplicationSettings.from_env()
    assert settings.github_client_id == ""
    assert settings.github_client_secret == ""
    assert settings.key_vault_url == ""


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



def test_github_connect_builds_callback_and_returns_to_onboarding(tmp_path):
    client = _client(tmp_path)
    headers = {"x-ms-client-principal-id": "owner-id"}

    started = client.get(
        "/connections/github/connect",
        headers=headers,
        follow_redirects=False,
    )
    assert started.status_code == 307
    params = parse_qs(urlparse(started.headers["location"]).query)
    assert params["redirect_uri"] == [
        "https://testserver/connections/github/callback"
    ]
    state = params["state"][0]

    completed = client.get(
        f"/connections/github/callback?code=code&state={state}",
        headers=headers,
        follow_redirects=False,
    )
    assert completed.status_code == 303
    assert completed.headers["location"] == "/app/onboarding?bootstrap=github"


def test_hosted_factory_requires_explicit_easy_auth_contract(tmp_path, monkeypatch):
    for name in (
        "KEY_VAULT_URL", "GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET", "GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET", "GOOGLE_REDIRECT_URI", "SPOTIFY_CLIENT_ID",
        "SPOTIFY_CLIENT_SECRET", "SPOTIFY_REDIRECT_URI", "DATABASE_SNAPSHOT", "ARCHIVE_MAX_BYTES",
    ):
        monkeypatch.delenv(f"TPA_{name}", raising=False)
    monkeypatch.setenv("TPA_DATABASE", str(tmp_path / "hosted.sqlite3"))
    monkeypatch.setenv("TPA_ARCHIVE_DIR", str(tmp_path / "archives"))
    monkeypatch.setenv("TPA_OWNER_OBJECT_ID", "owner-id")
    monkeypatch.delenv("TPA_TRUSTED_HOST_AUTH", raising=False)
    with pytest.raises(RuntimeError, match="requires TPA_TRUSTED_HOST_AUTH"):
        app_from_env()
    assert not (tmp_path / "hosted.sqlite3").exists()
    monkeypatch.setenv("TPA_TRUSTED_HOST_AUTH", "standalone")
    with pytest.raises(RuntimeError, match="requires TPA_TRUSTED_HOST_AUTH"):
        app_from_env()
    monkeypatch.setenv("TPA_TRUSTED_HOST_AUTH", "azure-container-apps-easy-auth")
    client = TestClient(app_from_env())
    assert client.get("/health").status_code == 200
    assert client.get("/connections").status_code == 401


def _state_dump(path):
    with closing(sqlite3.connect(path)) as database:
        return "\n".join(database.iterdump())


def test_composed_private_route_matrix_has_no_rejected_side_effects(tmp_path):
    client = _client(tmp_path)
    start = client.get("/connections/github/connect", headers=OWNER, follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    callback_params = {"code": OAUTH_CODE, "state": state}
    state_path = tmp_path / "state.sqlite3"
    before = _state_dump(state_path)
    archives = tmp_path / "archives"
    prior_files = sorted(path.relative_to(archives) for path in archives.rglob("*"))
    public = {"/", "/health"}
    denied = set()
    for path, operations in client.app.openapi()["paths"].items():
        if path in public:
            assert set(operations) == {"get"}
            assert client.get(path).status_code == 200
            continue
        concrete = path.replace("{provider_id}", "google").replace("{job_id}", "fixture")
        concrete = concrete.replace("{sha256}", "0" * 64)
        params = callback_params if concrete.endswith("/callback") else None
        for operation in operations:
            method = operation.upper()
            route = (method, concrete)
            denied.add(route)
            anonymous = client.request(*route, params=params, follow_redirects=False)
            expected = 307 if concrete.startswith("/app/") and method == "GET" else 401
            assert anonymous.status_code == expected, route
            if expected == 307:
                assert urlparse(anonymous.headers["location"]).path == "/.auth/login/aad"
            wrong = client.request(
                *route, params=params, headers={"x-ms-client-principal-id": "another-fixture-owner"},
                follow_redirects=False,
            )
            assert wrong.status_code == 403, route
    assert {
        ("GET", "/connections"), ("GET", "/connections/github/callback"),
        ("GET", "/connections/google/callback"),
        ("POST", "/archives/upload"), ("GET", "/app/"), ("GET", "/youtube/subscriptions"),
    } <= denied
    assert all(client.get(path).status_code == 404 for path in ("/docs", "/redoc", "/openapi.json"))
    assert _state_dump(state_path) == before
    assert sorted(path.relative_to(archives) for path in archives.rglob("*")) == prior_files
    callback = client.get(
        "/connections/github/callback", headers=OWNER, params=callback_params, follow_redirects=False,
    )
    assert callback.status_code == 303


def _complete_fixture_oauth(client):
    start = client.get("/connections/github/connect", headers=OWNER, follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    result = client.get(
        "/connections/github/callback", headers=OWNER, follow_redirects=False,
        params={"code": OAUTH_CODE, "state": state},
    )
    return result, state


def _assert_no_secret_values(text):
    for value in (
        ACCESS_TOKEN, REFRESH_TOKEN, PROVIDER_SESSION, CLIENT_SECRET, UNUSED_PASSWORD,
        UNUSED_PRIVATE_FIELD, OAUTH_CODE,
    ):
        assert value not in text


def test_synthetic_oauth_leaves_only_an_opaque_credential_reference(tmp_path, caplog):
    caplog.set_level("INFO")
    caplog.set_level("WARNING", logger="httpx")  # The in-process test transport logs request URLs.
    credentials = MemoryCredentials()
    snapshot = tmp_path / "snapshot" / "state.sqlite3"
    client = _client(tmp_path, credentials=credentials, snapshot=str(snapshot))
    result, state = _complete_fixture_oauth(client)
    assert result.status_code == 303
    private_ui = client.get("/app/connections", headers=OWNER)
    private_api = client.get("/connections", headers=OWNER)
    public = client.get("/")
    state_path = tmp_path / "state.sqlite3"
    assert snapshot.is_file()
    dump = _state_dump(state_path) + _state_dump(snapshot)
    for surface in (dump, caplog.text, result.text, private_ui.text, private_api.text, public.text):
        _assert_no_secret_values(surface)
        assert state not in surface
    with closing(sqlite3.connect(state_path)) as database:
        (reference,) = database.execute("SELECT credential_ref FROM provider_connections").fetchone()
    assert reference.startswith("memory://") and credentials.get(reference) == ACCESS_TOKEN


@pytest.mark.parametrize("failure", ["credential-store", "provider"])
def test_oauth_failure_is_sanitized_and_persists_no_credentials(tmp_path, caplog, failure):
    caplog.set_level("INFO")
    caplog.set_level("WARNING", logger="httpx")
    class FailingCredentials(MemoryCredentials):
        def put(self, *_args):
            raise RuntimeError(f"synthetic store refusal for {ACCESS_TOKEN}")

    def refuse(_request):
        raise RuntimeError(f"synthetic provider refusal for {PROVIDER_SESSION}")

    credentials = FailingCredentials() if failure == "credential-store" else MemoryCredentials()
    client = _client(tmp_path, credentials=credentials,
                     oauth_transport=refuse if failure == "provider" else _oauth)
    result, state = _complete_fixture_oauth(client)
    assert result.status_code == 400
    assert result.json() == {"detail": "GitHub authorization failed"}
    state_path = tmp_path / "state.sqlite3"
    dump = _state_dump(state_path)
    for surface in (dump, result.text, caplog.text):
        _assert_no_secret_values(surface)
        assert state not in surface
    assert credentials.values == {}
    with closing(sqlite3.connect(state_path)) as database:
        assert database.execute("SELECT COUNT(*) FROM provider_connections").fetchone()[0] == 0
