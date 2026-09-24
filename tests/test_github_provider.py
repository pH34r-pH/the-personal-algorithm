from datetime import UTC, datetime

import httpx

from personal_algorithm.github_connection import connect_github
from personal_algorithm.github_provider import GitHubProvider
from personal_algorithm.providers import BootstrapCheckpoint, ConnectionStatus


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


def _transport(request):
    if request.url.path == "/user":
        return httpx.Response(
            200,
            json={"id": 42, "login": "fixture", "html_url": "https://github.com/fixture"},
        )
    if request.url.path == "/user/repos":
        return httpx.Response(
            200,
            json=[
                {
                    "id": 99,
                    "full_name": "fixture/project",
                    "html_url": "https://github.com/fixture/project",
                    "description": "A fixture repository",
                    "topics": ["recommenders", "local-first"],
                    "language": "Python",
                    "stargazers_count": 3,
                    "fork": False,
                    "private": True,
                    "pushed_at": "2026-09-23T12:00:00Z",
                    "owner": {"login": "fixture"},
                }
            ],
        )
    return httpx.Response(404)


def test_connect_stores_token_behind_opaque_reference():
    credentials = MemoryCredentials()
    client = httpx.Client(
        base_url="https://api.github.com",
        transport=httpx.MockTransport(_transport),
    )
    provider = GitHubProvider(credentials, client=client)

    # Provider passed here validates through the same credential store, so stage
    # the temporary validation reference used by connect_github.
    credentials.values["memory://github-connect"] = "secret-token"
    connection = connect_github(
        "secret-token",
        credentials=credentials,
        provider=provider,
    )

    assert connection.status is ConnectionStatus.CONNECTED
    assert connection.account_label == "fixture"
    assert connection.credential_ref == "memory://github/42"
    assert connection.metadata["github_user_id"] == "42"
    assert connection.credential_ref != "secret-token"


def test_bootstrap_page_can_be_mapped_to_candidates():
    credentials = MemoryCredentials()
    credentials.values["memory://github/42"] = "secret-token"
    provider = GitHubProvider(
        credentials,
        client=httpx.Client(
            base_url="https://api.github.com",
            transport=httpx.MockTransport(_transport),
        ),
    )
    connection = connect_github(
        "secret-token",
        credentials=credentials,
        provider=provider,
    )

    checkpoint = provider.bootstrap(connection, BootstrapCheckpoint())
    assert checkpoint.imported == 1
    assert checkpoint.cursor is None

    candidates = provider.repository_candidates(
        checkpoint.metadata["repositories"],
        discovered_at=datetime(2026, 9, 23, 13, tzinfo=UTC),
    )
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.id == "github:repo:99"
    assert candidate.source == "github"
    assert candidate.topics == ("recommenders", "local-first")
    assert candidate.metadata["private"] is True
    assert candidate.provenance["endpoint"] == "/user/repos"
