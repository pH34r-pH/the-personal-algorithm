from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from personal_algorithm.github_auth import AuthorizationStateStore, GitHubAuthorization
from personal_algorithm.github_provider import GitHubProvider


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


def _github_api(request):
    if request.url.path == "/user":
        return httpx.Response(
            200,
            json={"id": 42, "login": "fixture", "html_url": "https://github.com/fixture"},
        )
    return httpx.Response(404)


def _oauth(request):
    assert request.url.path == "/login/oauth/access_token"
    return httpx.Response(200, json={"access_token": "oauth-token", "token_type": "bearer"})


def test_authorization_state_is_one_time_and_callback_stores_token():
    credentials = MemoryCredentials()
    states = AuthorizationStateStore()
    provider = GitHubProvider(
        credentials,
        client=httpx.Client(
            base_url="https://api.github.com",
            transport=httpx.MockTransport(_github_api),
        ),
    )
    auth = GitHubAuthorization(
        client_id="client-id",
        client_secret="client-secret",
        credentials=credentials,
        states=states,
        http=httpx.Client(transport=httpx.MockTransport(_oauth)),
        provider=provider,
    )

    url = auth.begin("https://personal.example/auth/github/callback")
    params = parse_qs(urlparse(url).query)
    state = params["state"][0]
    assert params["client_id"] == ["client-id"]

    connection = auth.complete(code="code", state=state)
    assert connection.account_label == "fixture"
    assert credentials.get(connection.credential_ref) == "oauth-token"

    with pytest.raises(ValueError):
        auth.complete(code="code", state=state)


def test_unknown_state_is_rejected_before_token_exchange():
    auth = GitHubAuthorization(
        client_id="client-id",
        client_secret="client-secret",
        credentials=MemoryCredentials(),
        states=AuthorizationStateStore(),
        http=httpx.Client(transport=httpx.MockTransport(_oauth)),
    )
    with pytest.raises(ValueError):
        auth.complete(code="code", state="not-issued")
