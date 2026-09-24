"""arXiv API adapter using its public Atom feed."""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import urlencode

import httpx

from ..models import Candidate
from .rss import FeedError, parse_feed

ARXIV_API = "https://export.arxiv.org/api/query"


def query_url(
    search_query: str,
    *,
    start: int = 0,
    max_results: int = 25,
    sort_by: str = "submittedDate",
    sort_order: str = "descending",
) -> str:
    if not search_query.strip():
        raise ValueError("arXiv search query must not be empty")
    if start < 0 or not 1 <= max_results <= 100:
        raise ValueError("arXiv pagination must use start >= 0 and 1 <= max_results <= 100")
    return f"{ARXIV_API}?{urlencode({
        'search_query': search_query,
        'start': start,
        'max_results': max_results,
        'sortBy': sort_by,
        'sortOrder': sort_order,
    })}"


def parse_arxiv(
    content: bytes,
    *,
    query: str,
    discovered_at: datetime | None = None,
) -> tuple[Candidate, ...]:
    """Parse arXiv Atom into Candidates and retain query provenance."""
    url = query_url(query)
    base = parse_feed(content, feed_url=url, discovered_at=discovered_at)
    output: list[Candidate] = []
    for candidate in base:
        arxiv_id = candidate.source_id.rsplit("/", 1)[-1]
        output.append(
            Candidate(
                id=f"arxiv:{arxiv_id}",
                source="arxiv",
                source_id=arxiv_id,
                canonical_url=candidate.canonical_url,
                title=candidate.title,
                discovered_at=candidate.discovered_at,
                content_type="paper",
                author=candidate.author,
                summary=candidate.summary,
                published_at=candidate.published_at,
                topics=candidate.topics,
                metadata=candidate.metadata,
                provenance={
                    **candidate.provenance,
                    "adapter": "arxiv",
                    "query": query,
                },
            )
        )
    return tuple(output)


def fetch_arxiv(
    query: str,
    *,
    max_results: int = 25,
    timeout: float = 20.0,
    discovered_at: datetime | None = None,
) -> tuple[Candidate, ...]:
    url = query_url(query, max_results=max_results)
    try:
        response = httpx.get(
            url,
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": "ThePersonalAlgorithm/0.1 (+self-hosted research discovery)"},
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise FeedError(f"failed to fetch arXiv query: {exc}") from exc
    return parse_arxiv(response.content, query=query, discovered_at=discovered_at or datetime.now(UTC))
