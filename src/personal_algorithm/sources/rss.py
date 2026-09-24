"""RSS/Atom source adapter.

Fetching and parsing are separated so tests and downstream callers can remain
hermetic and alternative transports can be supplied later.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from time import struct_time
from typing import Any

import feedparser
import httpx

from ..models import Candidate


class FeedError(ValueError):
    """A feed could not be fetched or parsed safely."""


def _timestamp(value: struct_time | None, fallback: datetime) -> datetime:
    if value is None:
        return fallback
    return datetime(*value[:6], tzinfo=UTC)


def _text(entry: Any, key: str) -> str | None:
    value = entry.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def parse_feed(
    content: bytes,
    *,
    feed_url: str,
    discovered_at: datetime | None = None,
) -> tuple[Candidate, ...]:
    discovered_at = discovered_at or datetime.now(UTC)
    parsed = feedparser.parse(content)
    if parsed.bozo and not parsed.entries:
        raise FeedError(f"invalid RSS/Atom feed: {parsed.bozo_exception}")

    feed_title = _text(parsed.feed, "title")
    candidates: list[Candidate] = []
    for entry in parsed.entries:
        canonical_url = _text(entry, "link")
        title = _text(entry, "title")
        if not canonical_url or not title:
            continue

        source_id = _text(entry, "id") or canonical_url
        digest = hashlib.sha256(f"{feed_url}\0{source_id}".encode()).hexdigest()[:24]
        published = _timestamp(
            entry.get("published_parsed") or entry.get("updated_parsed"),
            discovered_at,
        )
        author = _text(entry, "author")
        summary = _text(entry, "summary") or _text(entry, "description")
        tags = tuple(
            term
            for tag in entry.get("tags", [])
            if isinstance((term := tag.get("term")), str) and term
        )

        candidates.append(
            Candidate(
                id=f"rss:{digest}",
                source="rss",
                source_id=source_id,
                canonical_url=canonical_url,
                title=title,
                discovered_at=discovered_at,
                content_type="feed-entry",
                author=author,
                summary=summary,
                published_at=published,
                topics=tags,
                metadata={"feed_title": feed_title} if feed_title else {},
                provenance={"feed_url": feed_url, "format": parsed.version or "unknown"},
            )
        )
    return tuple(candidates)


def fetch_feed(
    feed_url: str,
    *,
    timeout: float = 15.0,
    discovered_at: datetime | None = None,
) -> tuple[Candidate, ...]:
    try:
        response = httpx.get(
            feed_url,
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": "ThePersonalAlgorithm/0.1 (+self-hosted feed reader)"},
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise FeedError(f"failed to fetch feed: {exc}") from exc
    return parse_feed(response.content, feed_url=str(response.url), discovered_at=discovered_at)
