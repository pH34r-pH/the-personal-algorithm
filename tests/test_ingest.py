import json
from datetime import UTC, datetime
from functools import partial

from personal_algorithm import cli
from personal_algorithm.ingest import ingest_feeds, ingest_ranking_run
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
        "feed-a": (_candidate("shared", "research"), _candidate("b", "other")),
        "feed-b": (_candidate("a", "research"), _candidate("shared", "research")),
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
    assert [item.candidate.id for item in ranked] == ["a", "shared", "b"]
    assert [item.score for item in ranked] == [1.0, 1.0, 0.1]
    assert all(item.policy_version == "ingest-v1" for item in ranked)
    assert all(item.ranked_at == NOW for item in ranked)
    assert store.connection.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 3
    assert store.connection.execute("SELECT COUNT(*) FROM rankings").fetchone()[0] == 3


def test_ingestion_command_returns_the_replay_identity(tmp_path, monkeypatch):
    database = tmp_path / "ranking.sqlite"
    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "database": str(database),
        "feeds": ["https://example.invalid/feed.xml"],
        "policy": {"version": "fixture", "weights": {}},
    }), encoding="utf-8")

    def fetcher(_):
        return _candidate("b", "other"), _candidate("a", "research")

    monkeypatch.setattr(cli, "ingest_ranking_run", partial(ingest_ranking_run, fetcher=fetcher, now=NOW))
    ranked = cli.run(str(config))
    assert [(item["candidate_id"], item["position"]) for item in ranked] == [("a", 1), ("b", 2)]
    run_id = ranked[0]["run_id"]
    assert all(item["run_id"] == run_id for item in ranked)
    store = Store(database)
    try:
        run = store.get_ranking_run(run_id)
        assert [item["candidate_input_sha256"] for item in ranked] == [
            member.snapshot.sha256 for member in run.members
        ]
        assert store.connection.execute("SELECT COUNT(*) FROM impressions").fetchone()[0] == 0
    finally:
        store.connection.close()
