"""Validate standalone SQLite snapshots without exposing their contents."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .leased_snapshot import SnapshotUnavailable


def validate_snapshot(path: Path) -> None:
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise SnapshotUnavailable("snapshot integrity check failed")
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )}
        if not {"candidates", "personal_events", "rankings", "interactions"} <= tables:
            raise SnapshotUnavailable("snapshot is missing required tables")
    finally:
        connection.close()
