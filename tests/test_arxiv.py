from datetime import UTC, datetime
from pathlib import Path

import pytest

from personal_algorithm.sources.arxiv import parse_arxiv, query_url

FIXTURE = Path(__file__).parent / "fixtures" / "sample-arxiv.xml"
NOW = datetime(2026, 9, 23, 13, tzinfo=UTC)


def test_arxiv_atom_maps_to_paper_candidate():
    candidates = parse_arxiv(
        FIXTURE.read_bytes(),
        query="all:recommendation",
        discovered_at=NOW,
    )

    assert len(candidates) == 1
    paper = candidates[0]
    assert paper.id == "arxiv:2609.12345v1"
    assert paper.source == "arxiv"
    assert paper.source_id == "2609.12345v1"
    assert paper.content_type == "paper"
    assert paper.canonical_url == "https://arxiv.org/abs/2609.12345v1"
    assert paper.topics == ("cs.IR", "cs.HC")
    assert paper.provenance["query"] == "all:recommendation"
    assert paper.provenance["adapter"] == "arxiv"


def test_query_url_is_bounded_and_encoded():
    url = query_url("cat:cs.IR AND all:personal algorithm", max_results=50)
    assert "max_results=50" in url
    assert "search_query=cat%3Acs.IR+AND+all%3Apersonal+algorithm" in url


@pytest.mark.parametrize(
    ("query", "start", "limit"),
    [("", 0, 25), ("all:test", -1, 25), ("all:test", 0, 0), ("all:test", 0, 101)],
)
def test_query_validation(query, start, limit):
    with pytest.raises(ValueError):
        query_url(query, start=start, max_results=limit)
