"""Source adapters."""

from .arxiv import fetch_arxiv, parse_arxiv, query_url
from .fixture import fixture_candidates
from .rss import FeedError, fetch_feed, parse_feed

__all__ = [
    "FeedError",
    "fetch_arxiv",
    "fetch_feed",
    "fixture_candidates",
    "parse_arxiv",
    "parse_feed",
    "query_url",
]
