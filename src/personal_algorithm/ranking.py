"""Transparent deterministic reference ranking."""

from __future__ import annotations

from datetime import UTC, datetime
from math import exp, isfinite

from .models import (
    Candidate,
    Explanation,
    Policy,
    RankedCandidate,
    ScoreContribution,
    finite_number_map,
)


class ReferenceRanker:
    """Small, inspectable baseline.

    It intentionally avoids embeddings and learned parameters. Better rankers
    can replace it later without changing the public contracts.
    """

    name = "reference-v0"

    def rank(
        self,
        candidate: Candidate,
        policy: Policy,
        *,
        now: datetime | None = None,
    ) -> RankedCandidate:
        now = now or datetime.now(UTC)
        policy = Policy(
            policy.version, finite_number_map(policy.weights, "weights"),
            finite_number_map(policy.topic_affinity, "topic_affinity"),
            finite_number_map(policy.source_affinity, "source_affinity"),
        )
        features = {
            "topic_affinity": self._topic_affinity(candidate, policy),
            "source_affinity": policy.source_affinity.get(candidate.source, 0.0),
            "recency": self._recency(candidate, now),
        }
        reasons = {
            "topic_affinity": "matches explicitly configured topic interests",
            "source_affinity": "reflects explicitly configured source preference",
            "recency": "rewards recently published or discovered candidates",
        }

        contributions = tuple(
            ScoreContribution(
                feature=name,
                value=value,
                weight=policy.weight(name),
                contribution=value * policy.weight(name),
                reason=reasons[name],
            )
            for name, value in features.items()
        )
        score = sum(item.contribution for item in contributions)
        if not isfinite(score):
            raise ValueError("reference ranker produced a non-finite score")
        explanation = Explanation(final_score=score, contributions=contributions)
        return RankedCandidate(
            candidate=candidate,
            score=score,
            ranker=self.name,
            policy_version=policy.version,
            explanation=explanation,
            ranked_at=now,
        )

    @staticmethod
    def _topic_affinity(candidate: Candidate, policy: Policy) -> float:
        if not candidate.topics:
            return 0.0
        return max((policy.topic_affinity.get(topic, 0.0) for topic in candidate.topics), default=0.0)

    @staticmethod
    def _recency(candidate: Candidate, now: datetime) -> float:
        timestamp = candidate.published_at or candidate.discovered_at
        if now.utcoffset() is None or timestamp.utcoffset() is None:
            raise ValueError("ranking evaluation and candidate timestamps must include a timezone")
        hours = max(0.0, (now - timestamp).total_seconds() / 3600)
        # Smooth, bounded decay: 1.0 now, ~0.5 after 72 hours.
        return exp(-hours * 0.6931471805599453 / 72.0)
