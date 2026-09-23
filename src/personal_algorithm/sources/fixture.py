"""Synthetic source used to prove the end-to-end M0 contract."""

from datetime import datetime, timezone

from ..models import Candidate


def fixture_candidates() -> tuple[Candidate, ...]:
    discovered = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    return (
        Candidate(
            id="fixture:paper-1",
            source="fixture",
            source_id="paper-1",
            canonical_url="https://example.invalid/paper-1",
            title="Transparent ranking for personal information systems",
            discovered_at=discovered,
            published_at=discovered,
            content_type="paper",
            topics=("recommenders", "personal-data"),
            provenance={"kind": "synthetic-fixture"},
        ),
        Candidate(
            id="fixture:project-1",
            source="fixture",
            source_id="project-1",
            canonical_url="https://example.invalid/project-1",
            title="A strange unrelated hardware project",
            discovered_at=discovered,
            published_at=discovered,
            content_type="project",
            topics=("hardware",),
            provenance={"kind": "synthetic-fixture"},
        ),
    )
