from datetime import UTC, datetime

from personal_algorithm.ingest import ingest_feeds
from personal_algorithm.models import Candidate, Policy
from personal_algorithm.store import Store

NOW = datetime(2026, 9, 23, 12, tzinfo=UTC)


def _candidate(candidate_id: str, topic: str) -> Candidate:
    return Candidate(
        id=candidate_id,
        source="rss",
        source_id=candidate_id,
        canonical_url=f"https://example.invalid/{candidate_id}",
        title=candidate_id,
        discovered_at=NOW,
        published_at=NOW,
        content_type="feed-entry",
        topics=(topic,),
        provenance={"feed_url": "synthetic"},
    )


def test_configured_feeds_are_deduplicated_ranked_and_persisted():
    feeds = {
        "feed-a": (_candidate("a", "research"), _candidate("shared", "research")),
        "feed-b": (_candidate("b", "other"), _candidate("shared", "research")),
    }

    def fetcher(url: str) -> tuple[Candidate, ...]:
        return feeds[url]

    store = Store()
    policy = Policy(
        version="ingest-v1",
        weights={"topic_affinity": 1.0},
        topic_affinity={"research": 1.0, "other": 0.1},
    )
    ranked = ingest_feeds(
        ["feed-a", "feed-b"],
        policy=policy,
        store=store,
        fetcher=fetcher,
        now=NOW,
    )

    assert len(ranked) == 3
    assert [item.candidate.id for item in ranked[:2]] == ["a", "shared"]
    assert store.connection.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 3
    assert store.connection.execute("SELECT COUNT(*) FROM rankings").fetchone()[0] == 3
