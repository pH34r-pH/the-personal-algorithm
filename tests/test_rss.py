from datetime import UTC, datetime
from pathlib import Path

import pytest

from personal_algorithm.sources.rss import FeedError, parse_feed

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 9, 23, 13, tzinfo=UTC)


def test_rss_emits_portable_candidates_with_provenance():
    content = (FIXTURES / "sample-rss.xml").read_bytes()
    candidates = parse_feed(
        content,
        feed_url="https://example.invalid/feed.xml",
        discovered_at=NOW,
    )

    assert len(candidates) == 2
    first = candidates[0]
    assert first.source == "rss"
    assert first.source_id == "post-1"
    assert first.canonical_url == "https://example.invalid/posts/recommenders"
    assert first.topics == ("recommenders", "personal-data")
    assert first.provenance["feed_url"] == "https://example.invalid/feed.xml"
    assert first.published_at == datetime(2026, 9, 23, 12, tzinfo=UTC)


def test_atom_uses_same_candidate_contract():
    content = (FIXTURES / "sample-atom.xml").read_bytes()
    candidate = parse_feed(
        content,
        feed_url="https://example.invalid/atom.xml",
        discovered_at=NOW,
    )[0]

    assert candidate.source == "rss"
    assert candidate.source_id == "urn:uuid:feed-entry-1"
    assert candidate.author == "Fixture Author"
    assert candidate.topics == ("rss",)
    assert candidate.provenance["format"].startswith("atom")


def test_ids_are_stable_for_same_feed_and_source_id():
    content = (FIXTURES / "sample-rss.xml").read_bytes()
    first = parse_feed(content, feed_url="https://example.invalid/feed.xml", discovered_at=NOW)
    second = parse_feed(content, feed_url="https://example.invalid/feed.xml", discovered_at=NOW)
    assert [item.id for item in first] == [item.id for item in second]


def test_invalid_feed_is_rejected():
    with pytest.raises(FeedError):
        parse_feed(b"not a feed", feed_url="https://example.invalid/bad", discovered_at=NOW)
