import pytest

from personal_algorithm.serialization import ValidationError
from personal_algorithm.source_config import ArxivSource, RssSource, source_from_dict


def test_typed_rss_source():
    source = source_from_dict({"type": "rss", "url": "https://example.invalid/feed.xml"})
    assert source == RssSource(url="https://example.invalid/feed.xml")


def test_typed_arxiv_source():
    source = source_from_dict(
        {"type": "arxiv", "query": "cat:cs.IR", "max_results": 40}
    )
    assert source == ArxivSource(query="cat:cs.IR", max_results=40)


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "rss", "url": "file:///tmp/feed"},
        {"type": "arxiv", "query": ""},
        {"type": "arxiv", "query": "all:test", "max_results": 101},
        {"type": "unknown"},
    ],
)
def test_invalid_typed_sources(payload):
    with pytest.raises(ValidationError):
        source_from_dict(payload)
