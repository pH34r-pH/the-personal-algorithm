"""Confidential-client OAuth for independently authorized provider connections."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode, urlsplit

import httpx

from .providers import Connection, ConnectionStatus, ProviderCapability, ProviderDescriptor


@dataclass(frozen=True, slots=True)
class OAuthSpec:
    id: str
    name: str
    authorize: str
    token: str
    identity: str
    scopes: tuple[str, ...]


SPECS = {
    "google": OAuthSpec(
        "google", "Google / YouTube", "https://accounts.google.com/o/oauth2/v2/auth",
        "https://oauth2.googleapis.com/token", "https://openidconnect.googleapis.com/v1/userinfo",
        ("openid", "https://www.googleapis.com/auth/youtube.readonly"),
    ),
    "spotify": OAuthSpec(
        "spotify", "Spotify", "https://accounts.spotify.com/authorize",
        "https://accounts.spotify.com/api/token", "https://api.spotify.com/v1/me",
        ("user-read-private", "user-read-recently-played"),
    ),
}


class DelegatedProvider:
    def __init__(self, spec: OAuthSpec) -> None:
        self.descriptor = ProviderDescriptor(
            id=spec.id, display_name=spec.name,
            capabilities=frozenset({ProviderCapability.SIGN_IN}), requested_scopes=spec.scopes,
        )

    def bootstrap(self, connection, checkpoint):
        raise NotImplementedError("provider sync is not implemented")


class DelegatedAuthorization:
    def __init__(self, spec, client_id, client_secret, redirect_uri, credentials, states):
        parsed = urlsplit(redirect_uri)
        if (not client_id or not client_secret or parsed.scheme != "https" or not parsed.hostname
                or parsed.query or parsed.fragment or parsed.username or parsed.password):
            raise ValueError("provider credentials and a fixed HTTPS callback are required")
        if not parsed.path.endswith(f"/connections/{spec.id}/callback"):
            raise ValueError("callback must match the provider")
        self.spec, self.client_id, self.client_secret = spec, client_id, client_secret
        self.redirect_uri, self.credentials, self.states = redirect_uri, credentials, states
        self.http = httpx.Client(timeout=20.0)

    def begin(self):
        state = self.states.create(self.redirect_uri)
        verifier = self._verifier(state)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        query = {"client_id": self.client_id, "redirect_uri": self.redirect_uri,
                 "response_type": "code", "scope": " ".join(self.spec.scopes),
                 "state": state, "code_challenge": challenge, "code_challenge_method": "S256"}
        if self.spec.id == "google":
            query.update({"access_type": "offline", "prompt": "consent"})
        return f"{self.spec.authorize}?{urlencode(query)}"

    def complete(self, *, code: str, state: str) -> Connection:
        if not code:
            raise ValueError("authorization code is required")
        pending = self.states.consume(state)
        if pending.redirect_uri != self.redirect_uri:
            raise ValueError("authorization state belongs to a different callback")
        response = self._token_request({
            "grant_type": "authorization_code", "code": code, "redirect_uri": self.redirect_uri,
            "code_verifier": self._verifier(state),
        })
        response.raise_for_status()
        payload = response.json()
        access = payload.get("access_token")
        refresh = payload.get("refresh_token")
        scopes = tuple(str(payload.get("scope", "")).split())
        if not isinstance(access, str) or not access or not isinstance(refresh, str) or not refresh:
            raise ValueError("provider did not issue offline credentials")
        if not set(self.spec.scopes).issubset(scopes):
            raise ValueError("required provider scopes were not granted")
        identity_response = self.http.get(
            self.spec.identity, headers={"Authorization": f"Bearer {access}"},
        )
        identity_response.raise_for_status()
        identity = identity_response.json()
        account_id = identity.get("sub" if self.spec.id == "google" else "id")
        if not isinstance(account_id, str) or not account_id:
            raise ValueError("provider identity missing")
        now = datetime.now(UTC)
        expires_in = int(payload.get("expires_in", 0))
        if expires_in <= 0:
            raise ValueError("invalid token lifetime")
        secret = json.dumps({"account_id": account_id, "access_token": access, "refresh_token": refresh,
                             "expires_at": (now + timedelta(seconds=expires_in)).isoformat()})
        ref = self.credentials.put(self.spec.id, account_id, secret)
        return Connection(
            provider_id=self.spec.id, account_label=self.spec.name,
            status=ConnectionStatus.CONNECTED, granted_scopes=scopes, connected_at=now,
            credential_ref=ref, metadata={},
        )

    def refresh(self, connection: Connection) -> None:
        if connection.provider_id != self.spec.id:
            raise ValueError("wrong provider connection")
        secret = json.loads(self.credentials.get(connection.credential_ref))
        response = self._token_request({
            "grant_type": "refresh_token", "refresh_token": secret["refresh_token"],
        })
        response.raise_for_status()
        payload = response.json()
        access = payload.get("access_token")
        seconds = int(payload.get("expires_in", 0))
        if not isinstance(access, str) or not access or seconds <= 0:
            raise ValueError("invalid refreshed credential")
        if "scope" in payload and not set(self.spec.scopes).issubset(str(payload["scope"]).split()):
            raise ValueError("required provider scopes are no longer granted")
        rotated = payload.get("refresh_token", secret["refresh_token"])
        if not isinstance(rotated, str) or not rotated:
            raise ValueError("invalid refresh credential")
        secret.update({"access_token": access, "refresh_token": rotated,
                       "expires_at": (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()})
        self.credentials.put(self.spec.id, secret["account_id"], json.dumps(secret))

    def _token_request(self, data: dict):
        if self.spec.id == "spotify":
            return self.http.post(self.spec.token, data=data, auth=(self.client_id, self.client_secret))
        return self.http.post(self.spec.token, data={**data, "client_id": self.client_id,
                                                     "client_secret": self.client_secret})

    def _verifier(self, state: str) -> str:
        value = hmac.new(self.client_secret.encode(),
                         f"tpa-pkce:{self.spec.id}:{state}".encode(), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode()
