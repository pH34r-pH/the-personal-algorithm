"""Durable, expiring, one-time OAuth authorization state."""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from .github_auth import PendingAuthorization
from .store import Store


class DurableAuthorizationStateStore:
    def __init__(self, store: Store, *, ttl: timedelta = timedelta(minutes=10)) -> None:
        if ttl <= timedelta(0):
            raise ValueError("authorization state TTL must be positive")
        self.store = store
        self.ttl = ttl
        self.store.connection.execute(
            """CREATE TABLE IF NOT EXISTS oauth_states (
                state_hash TEXT PRIMARY KEY,
                redirect_uri TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                consumed_at TEXT
            )"""
        )
        self.store.connection.commit()

    def create(self, redirect_uri: str, *, now: datetime | None = None) -> str:
        if not redirect_uri.startswith("https://"):
            raise ValueError("OAuth redirect URI must use HTTPS")
        now = now or datetime.now(UTC)
        state = secrets.token_urlsafe(32)
        digest = hashlib.sha256(state.encode()).hexdigest()
        self.store.connection.execute(
            """INSERT INTO oauth_states
               (state_hash, redirect_uri, expires_at, consumed_at)
               VALUES (?, ?, ?, NULL)""",
            (digest, redirect_uri, (now + self.ttl).isoformat()),
        )
        self.store.connection.commit()
        return state

    def consume(
        self,
        state: str,
        *,
        now: datetime | None = None,
    ) -> PendingAuthorization:
        now = now or datetime.now(UTC)
        digest = hashlib.sha256(state.encode()).hexdigest()

        # BEGIN IMMEDIATE serializes competing consumers before either can
        # observe an unconsumed row.
        self.store.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.store.connection.execute(
                """SELECT redirect_uri, expires_at, consumed_at
                   FROM oauth_states WHERE state_hash = ?""",
                (digest,),
            ).fetchone()
            if row is None or row[2] is not None:
                raise ValueError("invalid or already-consumed authorization state")
            expires_at = datetime.fromisoformat(row[1])
            if now >= expires_at:
                raise ValueError("authorization state has expired")
            self.store.connection.execute(
                "UPDATE oauth_states SET consumed_at = ? WHERE state_hash = ?",
                (now.isoformat(), digest),
            )
            self.store.connection.commit()
        except Exception:
            self.store.connection.rollback()
            raise

        return PendingAuthorization(state_hash=digest, redirect_uri=row[0])

    def prune(self, *, now: datetime | None = None) -> int:
        now = now or datetime.now(UTC)
        cursor = self.store.connection.execute(
            """DELETE FROM oauth_states
               WHERE expires_at <= ? OR consumed_at IS NOT NULL""",
            (now.isoformat(),),
        )
        self.store.connection.commit()
        return cursor.rowcount
