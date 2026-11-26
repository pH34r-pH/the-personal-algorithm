"""Portable core contracts.

These deliberately use only the Python standard library. Source adapters and
rankers may depend on richer libraries without forcing them on the core.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from math import isfinite
from typing import Any


class InteractionKind(StrEnum):
    OPENED = "opened"
    SKIMMED = "skimmed"
    FINISHED = "finished"
    SAVED = "saved"
    CITED = "cited"
    SHARED = "shared"
    LEARNED = "learned"
    ALREADY_KNEW = "already_knew"
    DISAGREED = "disagreed"
    MISLEADING = "misleading"
    IRRELEVANT = "irrelevant"
    MORE_LIKE_THIS = "more_like_this"
    LESS_LIKE_THIS = "less_like_this"


@dataclass(frozen=True, slots=True)
class Candidate:
    id: str
    source: str
    source_id: str
    canonical_url: str
    title: str
    discovered_at: datetime
    content_type: str
    author: str | None = None
    summary: str | None = None
    published_at: datetime | None = None
    topics: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PersonalEvent:
    id: str
    source: str
    event_type: str
    occurred_at: datetime | None = None
    subject: str | None = None
    canonical_url: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ScoreContribution:
    feature: str
    value: float
    weight: float
    contribution: float
    reason: str


@dataclass(frozen=True, slots=True)
class Explanation:
    final_score: float
    contributions: tuple[ScoreContribution, ...]


@dataclass(frozen=True, slots=True)
class RankedCandidate:
    candidate: Candidate
    score: float
    ranker: str
    policy_version: str
    explanation: Explanation
    ranked_at: datetime


@dataclass(frozen=True, slots=True)
class Interaction:
    candidate_id: str
    kind: InteractionKind
    occurred_at: datetime
    explicit: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)
    impression_id: str | None = None


@dataclass(frozen=True, slots=True)
class Impression:
    id: str
    run_id: str
    presented_at: datetime
    candidate_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Policy:
    version: str
    weights: dict[str, float]
    topic_affinity: dict[str, float] = field(default_factory=dict)
    source_affinity: dict[str, float] = field(default_factory=dict)

    def weight(self, feature: str) -> float:
        return self.weights.get(feature, 0.0)


def finite_number_map(value: Any, name: str) -> dict[str, float]:
    if not isinstance(value, dict):
        raise TypeError(f"{name} must map names to finite numbers")
    result = {}
    for key, number in value.items():
        if not isinstance(key, str) or not key.strip() or type(number) not in (int, float):
            raise ValueError(f"{name} must map names to finite numbers")
        try:
            converted = float(number)
        except OverflowError as exc:
            raise ValueError(f"{name} must map names to finite numbers") from exc
        if not isfinite(converted):
            raise ValueError(f"{name} must map names to finite numbers")
        result[key] = converted
    return result


def utcnow() -> datetime:
    return datetime.now(UTC)
