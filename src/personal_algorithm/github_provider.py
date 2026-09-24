"""GitHub authenticated provider and finite bootstrap importer."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from .credentials import CredentialStore
from .models import Candidate
from .providers import (
    BootstrapCheckpoint,
    Connection,
    ConnectionStatus,
    ProviderCapability,
    ProviderDescriptor,
)

GITHUB_API = "https://api.github.com"


class GitHubProvider:
    descriptor = ProviderDescriptor(
        id="github",
        display_name="GitHub",
        capabilities=frozenset(
            {
                ProviderCapability.SIGN_IN,
                ProviderCapability.CANDIDATE_DISCOVERY,
                ProviderCapability.HISTORY_IMPORT,
                ProviderCapability.CONTINUOUS_SYNC,
            }
        ),
        requested_scopes=("read:user",),
    )

    def __init__(
        self,
        credentials: CredentialStore,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self.credentials = credentials
        self.client = client or httpx.Client(
            base_url=GITHUB_API,
            timeout=20.0,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "ThePersonalAlgorithm/0.1",
            },
        )

    def identity(self, credential_ref: str) -> dict[str, Any]:
        token = self.credentials.get(credential_ref)
        response = self.client.get("/user", headers=self._auth(token))
        response.raise_for_status()
        user = response.json()
        return {
            "id": str(user["id"]),
            "login": user["login"],
            "html_url": user["html_url"],
        }

    def bootstrap(
        self,
        connection: Connection,
        checkpoint: BootstrapCheckpoint,
    ) -> BootstrapCheckpoint:
        """Import one page of the connected user's repositories.

        A bootstrap coordinator can call this repeatedly until cursor is None.
        """
        if connection.status is not ConnectionStatus.CONNECTED:
            raise ValueError("GitHub connection is not connected")

        page = int(checkpoint.cursor or "1")
        token = self.credentials.get(connection.credential_ref)
        response = self.client.get(
            "/user/repos",
            params={
                "visibility": "all",
                "affiliation": "owner,collaborator,organization_member",
                "sort": "updated",
                "direction": "desc",
                "per_page": 100,
                "page": page,
            },
            headers=self._auth(token),
        )
        response.raise_for_status()
        repositories = response.json()
        next_cursor = str(page + 1) if len(repositories) == 100 else None
        return BootstrapCheckpoint(
            cursor=next_cursor,
            imported=checkpoint.imported + len(repositories),
            skipped=checkpoint.skipped,
            metadata={
                **checkpoint.metadata,
                "last_page": page,
                "repositories": repositories,
            },
        )

    def repository_candidates(
        self,
        repositories: list[dict[str, Any]],
        *,
        discovered_at: datetime | None = None,
    ) -> tuple[Candidate, ...]:
        discovered_at = discovered_at or datetime.now(UTC)
        output: list[Candidate] = []
        for repo in repositories:
            topics = tuple(repo.get("topics") or ())
            output.append(
                Candidate(
                    id=f"github:repo:{repo['id']}",
                    source="github",
                    source_id=str(repo["id"]),
                    canonical_url=repo["html_url"],
                    title=repo["full_name"],
                    discovered_at=discovered_at,
                    published_at=self._parse_time(repo.get("pushed_at") or repo.get("updated_at")),
                    content_type="repository",
                    author=repo.get("owner", {}).get("login"),
                    summary=repo.get("description"),
                    topics=topics,
                    metadata={
                        "language": repo.get("language"),
                        "stars": repo.get("stargazers_count"),
                        "fork": bool(repo.get("fork")),
                        "private": bool(repo.get("private")),
                    },
                    provenance={
                        "provider": "github",
                        "endpoint": "/user/repos",
                        "repository_id": repo["id"],
                    },
                )
            )
        return tuple(output)

    @staticmethod
    def _auth(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    @staticmethod
    def _parse_time(value: str | None) -> datetime | None:
        return datetime.fromisoformat(value) if value else None
