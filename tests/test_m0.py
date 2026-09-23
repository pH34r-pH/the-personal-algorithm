from datetime import datetime, timezone

from personal_algorithm.models import Interaction, InteractionKind, Policy
from personal_algorithm.ranking import ReferenceRanker
from personal_algorithm.sources import fixture_candidates
from personal_algorithm.store import Store


NOW = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)


def test_reference_ranker_is_deterministic_and_explained():
    candidate = fixture_candidates()[0]
    policy = Policy(
        version="test-v1",
        weights={"topic_affinity": 0.7, "source_affinity": 0.1, "recency": 0.2},
        topic_affinity={"recommenders": 1.0},
        source_affinity={"fixture": 0.5},
    )
    ranker = ReferenceRanker()

    first = ranker.rank(candidate, policy, now=NOW)
    second = ranker.rank(candidate, policy, now=NOW)

    assert first.score == second.score
    assert first.score == first.explanation.final_score
    assert sum(c.contribution for c in first.explanation.contributions) == first.score
    assert {c.feature for c in first.explanation.contributions} == {
        "topic_affinity",
        "source_affinity",
        "recency",
    }


def test_m0_vertical_slice_persists_candidate_ranking_and_feedback():
    candidate = fixture_candidates()[0]
    policy = Policy(version="test-v1", weights={"recency": 1.0})
    ranked = ReferenceRanker().rank(candidate, policy, now=NOW)
    store = Store()

    store.put_candidate(candidate)
    store.put_ranking(ranked)
    store.put_interaction(
        Interaction(
            candidate_id=candidate.id,
            kind=InteractionKind.LEARNED,
            occurred_at=NOW,
        )
    )

    assert store.connection.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 1
    assert store.connection.execute("SELECT COUNT(*) FROM rankings").fetchone()[0] == 1
    assert store.connection.execute("SELECT COUNT(*) FROM interactions").fetchone()[0] == 1

    explanation = store.connection.execute(
        "SELECT explanation_json FROM rankings"
    ).fetchone()[0]
    assert '"recency"' in explanation
