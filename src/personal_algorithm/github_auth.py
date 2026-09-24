"""Browser-facing GitHub authorization state and callback exchange."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

from .credentials import CredentialStore
from .github_connection import connect_github
from .github_provider import GitHubProvider
from .providers import Connection

GITHUB_AUTHORIZE = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN = "https://github.com/login/oauth/access_token"


@dataclass(frozen=True, slots=True)
class PendingAuthorization:
    state_hash: str
    redirect_uri: str


class AuthorizationStateStore:
    """Single-process state store.

    The interface is intentionally tiny so deployment can replace it with
    durable/expiring state before multi-instance hosting.
    """

    def __init__(self) -> None:
        self._pending: dict[str, PendingAuthorization] = {}

    def create(self, redirect_uri: str) -> str:
        state = secrets.token_urlsafe(32)
        digest = hashlib.sha256(state.encode()).hexdigest()
        self._pending[digest] = PendingAuthorization(
            state_hash=digest,
            redirect_uri=redirect_uri,
        )
        return state

    def consume(self, state: str) -> PendingAuthorization:
        digest = hashlib.sha256(state.encode()).hexdigest()
        try:
            return self._pending.pop(digest)
        except KeyError as exc:
            raise ValueError("invalid or already-consumed authorization state") from exc


class GitHubAuthorization:
    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        credentials: CredentialStore,
        states: AuthorizationStateStore,
        http: httpx.Client | None = None,
        provider: GitHubProvider | None = None,
    ) -> None:
        if not client_id or not client_secret:
            raise ValueError("GitHub client credentials are required")
        self.client_id = client_id
        self.client_secret = client_secret
        self.credentials = credentials
        self.states = states
        self.http = http or httpx.Client(timeout=20.0)
        self.provider = provider

    def begin(self, redirect_uri: str) -> str:
        state = self.states.create(redirect_uri)
        query = urlencode(
            {
                "client_id": self.client_id,
                "redirect_uri": redirect_uri,
                "state": state,
            }
        )
        return f"{GITHUB_AUTHORIZE}?{query}"

    def complete(self, *, code: str, state: str) -> Connection:
        if not code:
            raise ValueError("GitHub authorization code is required")
        pending = self.states.consume(state)
        response = self.http.post(
            GITHUB_TOKEN,
            headers={"Accept": "application/json"},
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "code": code,
                "redirect_uri": pending.redirect_uri,
            },
        )
        response.raise_for_status()
        payload = response.json()
        access_token = payload.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise ValueError("GitHub did not return an access token")
        return connect_github(
            access_token,
            credentials=self.credentials,
            provider=self.provider,
        )
