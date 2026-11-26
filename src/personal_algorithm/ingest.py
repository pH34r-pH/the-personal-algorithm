"""Configured multi-feed ingestion and ranking."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import UTC, datetime

from .models import Candidate, Policy, RankedCandidate
from .ranking_runs import create_ranking_run
from .source_config import SourceConfig, acquire
from .store import Store

SourceFetcher = Callable[[SourceConfig], tuple[Candidate, ...]]


def ingest_sources(
    sources: Iterable[SourceConfig],
    *,
    policy: Policy,
    store: Store,
    fetcher: SourceFetcher = acquire,
    now: datetime | None = None,
) -> tuple[RankedCandidate, ...]:
    """Fetch, persist, rank, and return candidates across configured feeds.

    Duplicate candidate IDs collapse to one ranking in a single ingestion run.
    The persistent store remains the audit trail for each ranking run.
    """
    now = now or datetime.now(UTC)
    candidates: dict[str, Candidate] = {}

    for source in sources:
        for candidate in fetcher(source):
            candidates[candidate.id] = candidate

    if not candidates:
        return ()
    run = create_ranking_run(candidates.values(), policy, now=now)
    store.put_ranking_run(run)
    return tuple(member.ranked for member in run.members)


# Compatibility helper for the first M1 RSS-only API.
def ingest_feeds(feed_urls, *, policy, store, fetcher=None, now=None):
    from .source_config import RssSource

    sources = tuple(RssSource(url=url) for url in feed_urls)
    if fetcher is None:
        return ingest_sources(sources, policy=policy, store=store, now=now)

    def adapted(source):
        return fetcher(source.url)

    return ingest_sources(sources, policy=policy, store=store, fetcher=adapted, now=now)
