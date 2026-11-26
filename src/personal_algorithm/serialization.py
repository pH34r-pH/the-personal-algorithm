"""Validation and JSON-safe serialization at the process boundary."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .models import (
    Candidate,
    Interaction,
    InteractionKind,
    Policy,
    RankedCandidate,
    finite_number_map,
)


class ValidationError(ValueError):
    """Input did not satisfy a public contract."""


def _required_text(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{key} must be a non-empty string")
    return value


def _datetime(value: Any, key: str) -> datetime:
    if not isinstance(value, str):
        raise ValidationError(f"{key} must be an ISO-8601 datetime")
    try:
        result = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError(f"{key} must be an ISO-8601 datetime") from exc
    if result.tzinfo is None:
        raise ValidationError(f"{key} must include a timezone")
    return result


def candidate_from_dict(data: dict[str, Any]) -> Candidate:
    if not isinstance(data, dict):
        raise ValidationError("candidate must be an object")
    discovered = _datetime(data.get("discovered_at"), "discovered_at")
    published_raw = data.get("published_at")
    topics = data.get("topics", [])
    if not isinstance(topics, list) or not all(isinstance(item, str) for item in topics):
        raise ValidationError("topics must be a list of strings")
    return Candidate(
        id=_required_text(data, "id"),
        source=_required_text(data, "source"),
        source_id=_required_text(data, "source_id"),
        canonical_url=_required_text(data, "canonical_url"),
        title=_required_text(data, "title"),
        discovered_at=discovered,
        content_type=_required_text(data, "content_type"),
        author=data.get("author"),
        summary=data.get("summary"),
        published_at=_datetime(published_raw, "published_at") if published_raw else None,
        topics=tuple(topics),
        metadata=data.get("metadata", {}),
        provenance=data.get("provenance", {}),
    )


def policy_from_dict(data: dict[str, Any]) -> Policy:
    if not isinstance(data, dict):
        raise ValidationError("policy must be an object")
    try:
        return Policy(
            version=_required_text(data, "version"),
            weights=finite_number_map(data.get("weights"), "weights"),
            topic_affinity=finite_number_map(data.get("topic_affinity", {}), "topic_affinity"),
            source_affinity=finite_number_map(data.get("source_affinity", {}), "source_affinity"),
        )
    except (TypeError, ValueError) as exc:
        raise ValidationError(str(exc)) from exc


def interaction_from_dict(data: dict[str, Any]) -> Interaction:
    try:
        kind = InteractionKind(_required_text(data, "kind"))
    except ValueError as exc:
        raise ValidationError("kind is not a supported interaction") from exc
    return Interaction(
        candidate_id=_required_text(data, "candidate_id"),
        kind=kind,
        occurred_at=_datetime(data.get("occurred_at"), "occurred_at"),
        explicit=bool(data.get("explicit", True)),
        metadata=data.get("metadata", {}),
        impression_id=_required_text(data, "impression_id") if data.get("impression_id") is not None else None,
    )


def presentation_from_dict(data: dict[str, Any]) -> tuple[str, tuple[str, ...], datetime, str | None]:
    ids = data.get("candidate_ids")
    if not isinstance(ids, list) or not ids or not all(isinstance(value, str) and value.strip() for value in ids):
        raise ValidationError("candidate_ids must be an ordered nonempty list of candidate IDs")
    return (
        _required_text(data, "run_id"), tuple(ids), _datetime(data.get("presented_at"), "presented_at"),
        _required_text(data, "id") if data.get("id") is not None else None,
    )


def ranked_to_dict(ranked: RankedCandidate) -> dict[str, Any]:
    return {
        "candidate_id": ranked.candidate.id,
        "score": ranked.score,
        "ranker": ranked.ranker,
        "policy_version": ranked.policy_version,
        "ranked_at": ranked.ranked_at.isoformat(),
        "explanation": {
            "final_score": ranked.explanation.final_score,
            "contributions": [
                {
                    "feature": item.feature,
                    "value": item.value,
                    "weight": item.weight,
                    "contribution": item.contribution,
                    "reason": item.reason,
                }
                for item in ranked.explanation.contributions
            ],
        },
    }
