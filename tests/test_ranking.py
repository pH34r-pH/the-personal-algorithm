from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

from personal_algorithm.models import Candidate, Policy
from personal_algorithm.ranking import ReferenceRanker
from personal_algorithm.ranking_runs import create_ranking_run, replay_ranking_run
from personal_algorithm.serialization import policy_from_dict, ranked_to_dict
from personal_algorithm.sources import fixture_candidates

NOW = datetime(2026, 1, 1, 12, tzinfo=UTC)


def _candidate(candidate_id: str, *, published_at: datetime | None) -> Candidate:
    return Candidate(
        id=candidate_id,
        source="synthetic",
        source_id=candidate_id,
        canonical_url=f"https://example.invalid/{candidate_id}",
        title=candidate_id,
        discovered_at=NOW - timedelta(days=30),
        published_at=published_at,
        content_type="test",
    )


def test_recency_uses_published_or_discovered_time_and_has_bounded_decay():
    policy = Policy(version="recency-v1", weights={"recency": 1.0})
    ranker = ReferenceRanker()

    current = ranker.rank(_candidate("current", published_at=NOW), policy, now=NOW)
    future = ranker.rank(
        _candidate("future", published_at=NOW + timedelta(hours=2)), policy, now=NOW
    )
    three_days_old = ranker.rank(
        _candidate("old", published_at=NOW - timedelta(hours=72)), policy, now=NOW
    )
    discovered_only = ranker.rank(
        _candidate("discovered", published_at=None),
        policy,
        now=NOW,
    )

    assert current.score == pytest.approx(1.0)
    assert future.score == pytest.approx(1.0)
    assert three_days_old.score == pytest.approx(0.5)
    assert discovered_only.score == pytest.approx(
        2 ** (-30 * 24 / 72),
    )


def test_rank_exposes_weighted_values_reasons_and_policy_version():
    candidate = replace(
        fixture_candidates()[0],
        discovered_at=NOW - timedelta(hours=72),
        published_at=NOW - timedelta(hours=72),
    )
    policy = Policy(
        version="explanation-v1",
        weights={"topic_affinity": 0.7, "source_affinity": 0.1, "recency": 0.2},
        topic_affinity={"recommenders": 1.0},
        source_affinity={"fixture": 0.5},
    )

    ranked = ReferenceRanker().rank(candidate, policy, now=NOW)
    contributions = {item.feature: item for item in ranked.explanation.contributions}

    assert contributions["topic_affinity"].value == 1.0
    assert contributions["topic_affinity"].weight == 0.7
    assert contributions["topic_affinity"].contribution == 0.7
    assert contributions["source_affinity"].value == 0.5
    assert contributions["source_affinity"].weight == 0.1
    assert contributions["source_affinity"].contribution == 0.05
    assert contributions["recency"].value == pytest.approx(0.5)
    assert contributions["recency"].weight == 0.2
    assert ranked.policy_version == "explanation-v1"
    assert ranked.explanation.final_score == pytest.approx(ranked.score)
    assert all(item.reason for item in contributions.values())


def test_topic_affinity_is_zero_for_candidates_without_topics():
    candidate = replace(_candidate("untagged", published_at=NOW), topics=())
    policy = Policy(
        version="empty-topics-v1",
        weights={"topic_affinity": 1.0},
        topic_affinity={"recommenders": 1.0},
    )

    ranked = ReferenceRanker().rank(candidate, policy, now=NOW)

    assert ranked.score == 0.0
    assert ranked.explanation.contributions[0].value == 0.0


def test_frozen_run_has_stable_ties_and_replays_after_inputs_change():
    later_offset = timezone(timedelta(hours=2))
    first = _candidate("a", published_at=NOW)
    second = _candidate("b", published_at=NOW.astimezone(later_offset))
    policy = Policy(version="synthetic-v1", weights={"recency": -2, "topic_affinity": 0})
    original = create_ranking_run([second, first], policy, now=NOW)
    equivalent = create_ranking_run([first, second], policy, now=NOW.astimezone(later_offset))
    assert original.id != equivalent.id
    assert original.members == equivalent.members
    assert original.policy == equivalent.policy
    assert [member.ranked.candidate.id for member in original.members] == ["a", "b"]
    assert original.context_version is None and original.context_sha256 is None
    policy.weights["recency"] = 100
    first.metadata["edited-after-run"] = True
    revised = create_ranking_run([first, second], policy, now=NOW + timedelta(days=1))
    assert revised.policy.sha256 != original.policy.sha256
    assert revised.members[0].snapshot.sha256 != original.members[0].snapshot.sha256
    replayed = replay_ranking_run(original)
    assert [ranked_to_dict(value) for value in replayed] == [
        ranked_to_dict(member.ranked) for member in original.members
    ]


@pytest.mark.parametrize(("field", "value"), [
    ("weights", {"recency": float("inf")}),
    ("topic_affinity", {"topic": float("nan")}),
    ("source_affinity", {"fixture": "3.0"}),
    ("weights", {"recency": True}),
])
def test_invalid_weights_or_affinities_fail_before_ranking(field, value):
    payload = {"version": "bad", "weights": {}, field: value}
    with pytest.raises(ValueError, match="finite numbers"):
        policy_from_dict(payload)
    policy = Policy(**payload)
    with pytest.raises(ValueError, match="finite numbers"):
        create_ranking_run([_candidate("candidate", published_at=NOW)], policy, now=NOW)
