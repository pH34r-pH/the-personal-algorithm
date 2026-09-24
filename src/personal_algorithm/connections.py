"""Persistence for provider Connections without credential material."""

from __future__ import annotations

import json
from datetime import datetime

from .providers import Connection, ConnectionStatus
from .store import Store


class ConnectionStore:
    def __init__(self, store: Store) -> None:
        self.store = store
        self.store.connection.execute(
            """CREATE TABLE IF NOT EXISTS provider_connections (
                provider_id TEXT PRIMARY KEY,
                account_label TEXT NOT NULL,
                status TEXT NOT NULL,
                granted_scopes_json TEXT NOT NULL,
                connected_at TEXT NOT NULL,
                credential_ref TEXT NOT NULL,
                metadata_json TEXT NOT NULL
            )"""
        )
        self.store.connection.commit()

    def put(self, connection: Connection) -> None:
        self.store.connection.execute(
            """INSERT INTO provider_connections
               (provider_id, account_label, status, granted_scopes_json,
                connected_at, credential_ref, metadata_json)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(provider_id) DO UPDATE SET
                 account_label=excluded.account_label,
                 status=excluded.status,
                 granted_scopes_json=excluded.granted_scopes_json,
                 connected_at=excluded.connected_at,
                 credential_ref=excluded.credential_ref,
                 metadata_json=excluded.metadata_json""",
            (
                connection.provider_id,
                connection.account_label,
                connection.status.value,
                json.dumps(connection.granted_scopes),
                connection.connected_at.isoformat(),
                connection.credential_ref,
                json.dumps(connection.metadata, sort_keys=True),
            ),
        )
        self.store.connection.commit()

    def get(self, provider_id: str) -> Connection:
        row = self.store.connection.execute(
            """SELECT account_label, status, granted_scopes_json, connected_at,
                      credential_ref, metadata_json
               FROM provider_connections WHERE provider_id = ?""",
            (provider_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"provider is not connected: {provider_id}")
        return Connection(
            provider_id=provider_id,
            account_label=row[0],
            status=ConnectionStatus(row[1]),
            granted_scopes=tuple(json.loads(row[2])),
            connected_at=datetime.fromisoformat(row[3]),
            credential_ref=row[4],
            metadata=json.loads(row[5]),
        )

    def list(self) -> tuple[Connection, ...]:
        ids = [
            row[0]
            for row in self.store.connection.execute(
                "SELECT provider_id FROM provider_connections ORDER BY provider_id"
            )
        ]
        return tuple(self.get(provider_id) for provider_id in ids)
