"""Source adapters."""

from .fixture import fixture_candidates
from .rss import FeedError, fetch_feed, parse_feed

__all__ = ["FeedError", "fetch_feed", "fixture_candidates", "parse_feed"]
