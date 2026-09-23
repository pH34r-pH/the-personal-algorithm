"""SQLite event and recommendation store."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import Candidate, Interaction, RankedCandidate


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS candidates (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    title TEXT NOT NULL,
    discovered_at TEXT NOT NULL,
    content_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    UNIQUE(source, source_id)
);

CREATE TABLE IF NOT EXISTS rankings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id TEXT NOT NULL REFERENCES candidates(id),
    score REAL NOT NULL,
    ranker TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    ranked_at TEXT NOT NULL,
    explanation_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS interactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id TEXT NOT NULL REFERENCES candidates(id),
    kind TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    explicit INTEGER NOT NULL,
    metadata_json TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.connection = sqlite3.connect(str(path))
        self.connection.executescript(SCHEMA)

    def put_candidate(self, candidate: Candidate) -> None:
        payload = {
            "author": candidate.author,
            "summary": candidate.summary,
            "published_at": candidate.published_at.isoformat() if candidate.published_at else None,
            "topics": candidate.topics,
            "metadata": candidate.metadata,
            "provenance": candidate.provenance,
        }
        self.connection.execute(
            """INSERT INTO candidates
               (id, source, source_id, canonical_url, title, discovered_at, content_type, payload_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 canonical_url=excluded.canonical_url,
                 title=excluded.title,
                 discovered_at=excluded.discovered_at,
                 content_type=excluded.content_type,
                 payload_json=excluded.payload_json""",
            (
                candidate.id,
                candidate.source,
                candidate.source_id,
                candidate.canonical_url,
                candidate.title,
                candidate.discovered_at.isoformat(),
                candidate.content_type,
                json.dumps(payload, sort_keys=True),
            ),
        )
        self.connection.commit()

    def put_ranking(self, ranked: RankedCandidate) -> None:
        explanation = {
            "final_score": ranked.explanation.final_score,
            "contributions": [
                {
                    "feature": c.feature,
                    "value": c.value,
                    "weight": c.weight,
                    "contribution": c.contribution,
                    "reason": c.reason,
                }
                for c in ranked.explanation.contributions
            ],
        }
        self.connection.execute(
            """INSERT INTO rankings
               (candidate_id, score, ranker, policy_version, ranked_at, explanation_json)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                ranked.candidate.id,
                ranked.score,
                ranked.ranker,
                ranked.policy_version,
                ranked.ranked_at.isoformat(),
                json.dumps(explanation, sort_keys=True),
            ),
        )
        self.connection.commit()

    def put_interaction(self, interaction: Interaction) -> None:
        self.connection.execute(
            """INSERT INTO interactions
               (candidate_id, kind, occurred_at, explicit, metadata_json)
               VALUES (?, ?, ?, ?, ?)""",
            (
                interaction.candidate_id,
                interaction.kind.value,
                interaction.occurred_at.isoformat(),
                int(interaction.explicit),
                json.dumps(interaction.metadata, sort_keys=True),
            ),
        )
        self.connection.commit()
