"""Runnable M0 fixture demonstration."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from .models import Policy
from .ranking import ReferenceRanker
from .serialization import ranked_to_dict
from .sources import fixture_candidates


def run_demo() -> list[dict]:
    policy = Policy(
        version="demo-v1",
        weights={"topic_affinity": 0.7, "source_affinity": 0.1, "recency": 0.2},
        topic_affinity={"recommenders": 1.0, "personal-data": 0.8, "hardware": 0.2},
        source_affinity={"fixture": 0.5},
    )
    ranker = ReferenceRanker()
    now = datetime(2026, 1, 1, 12, tzinfo=UTC)
    ranked = [ranker.rank(candidate, policy, now=now) for candidate in fixture_candidates()]
    ranked.sort(key=lambda item: item.score, reverse=True)
    return [ranked_to_dict(item) for item in ranked]


def main() -> None:
    print(json.dumps(run_demo(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
