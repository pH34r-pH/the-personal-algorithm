"""Typed source configuration and acquisition dispatch."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import Candidate
from .serialization import ValidationError
from .sources.arxiv import fetch_arxiv
from .sources.rss import fetch_feed


@dataclass(frozen=True, slots=True)
class RssSource:
    url: str
    kind: str = "rss"


@dataclass(frozen=True, slots=True)
class ArxivSource:
    query: str
    max_results: int = 25
    kind: str = "arxiv"


SourceConfig = RssSource | ArxivSource


def source_from_dict(data: dict[str, Any]) -> SourceConfig:
    kind = data.get("type")
    if kind == "rss":
        url = data.get("url")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            raise ValidationError("rss source requires an HTTP(S) url")
        return RssSource(url=url)

    if kind == "arxiv":
        query = data.get("query")
        limit = data.get("max_results", 25)
        if not isinstance(query, str) or not query.strip():
            raise ValidationError("arxiv source requires a non-empty query")
        if not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValidationError("arxiv max_results must be an integer from 1 to 100")
        return ArxivSource(query=query, max_results=limit)

    raise ValidationError(f"unsupported source type: {kind!r}")


def acquire(source: SourceConfig) -> tuple[Candidate, ...]:
    if isinstance(source, RssSource):
        return fetch_feed(source.url)
    if isinstance(source, ArxivSource):
        return fetch_arxiv(source.query, max_results=source.max_results)
    raise TypeError(f"unsupported source config: {type(source)!r}")
