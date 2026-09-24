"""Configured multi-feed ingestion and ranking."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import UTC, datetime

from .models import Candidate, Policy, RankedCandidate
from .ranking import ReferenceRanker
from .sources.rss import fetch_feed
from .store import Store

FeedFetcher = Callable[[str], tuple[Candidate, ...]]


def ingest_feeds(
    feed_urls: Iterable[str],
    *,
    policy: Policy,
    store: Store,
    fetcher: FeedFetcher = fetch_feed,
    now: datetime | None = None,
) -> tuple[RankedCandidate, ...]:
    """Fetch, persist, rank, and return candidates across configured feeds.

    Duplicate candidate IDs collapse to one ranking in a single ingestion run.
    The persistent store remains the audit trail for each ranking run.
    """
    now = now or datetime.now(UTC)
    ranker = ReferenceRanker()
    candidates: dict[str, Candidate] = {}

    for url in feed_urls:
        for candidate in fetcher(url):
            candidates[candidate.id] = candidate

    ranked: list[RankedCandidate] = []
    for candidate in candidates.values():
        store.put_candidate(candidate)
        item = ranker.rank(candidate, policy, now=now)
        store.put_ranking(item)
        ranked.append(item)

    ranked.sort(key=lambda item: (-item.score, item.candidate.id))
    return tuple(ranked)
