"""Immutable inputs and reproducible reference ranking runs."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from math import isclose, isfinite
from typing import Literal
from uuid import uuid4

from .models import Candidate, Policy, RankedCandidate
from .ranking import ReferenceRanker
from .serialization import candidate_from_dict, policy_from_dict, ranked_to_dict

_TOLERANCE = 1e-12
InputKind = Literal["candidate", "policy"]


def _canonical(value: dict) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _utc(value: datetime) -> datetime:
    if value.utcoffset() is None:
        raise ValueError("ranking input timestamps must include a timezone")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class RankingInput:
    kind: InputKind
    sha256: str
    content: str

    @classmethod
    def freeze(cls, kind: InputKind, value: dict) -> RankingInput:
        content = _canonical({"schema_version": 1, "kind": kind, "value": value})
        return cls(kind, hashlib.sha256(content.encode()).hexdigest(), content)

    def load(self, kind: InputKind) -> dict:
        if (self.kind != kind or not isinstance(self.content, str)
                or hashlib.sha256(self.content.encode()).hexdigest() != self.sha256):
            raise ValueError("ranking input kind or digest differs from the frozen snapshot")
        value = json.loads(self.content)
        if (not isinstance(value, dict) or set(value) != {"schema_version", "kind", "value"}
                or type(value["schema_version"]) is not int or value["schema_version"] != 1
                or value["kind"] != kind or not isinstance(value["value"], dict)
                or _canonical(value) != self.content):
            raise ValueError("ranking input snapshot is malformed")
        return value["value"]


@dataclass(frozen=True, slots=True)
class RankingMember:
    snapshot: RankingInput
    ranked: RankedCandidate


@dataclass(frozen=True, slots=True)
class RankingRun:
    id: str
    evaluated_at: datetime
    ranker: str
    policy: RankingInput
    members: tuple[RankingMember, ...]
    context_version: str | None = None
    context_sha256: str | None = None


def _candidate_snapshot(candidate: Candidate) -> RankingInput:
    value = asdict(candidate)
    value["discovered_at"] = _utc(candidate.discovered_at).isoformat()
    value["published_at"] = _utc(candidate.published_at).isoformat() if candidate.published_at else None
    return RankingInput.freeze("candidate", value)


def _finite_number(value) -> bool:
    try:
        return type(value) in (int, float) and isfinite(value)
    except OverflowError:
        return False


def validate_score(ranked: RankedCandidate) -> None:
    explanation = ranked.explanation
    numeric = [ranked.score, explanation.final_score]
    for value in explanation.contributions:
        numeric.extend((value.value, value.weight, value.contribution))
    if not all(map(_finite_number, numeric)):
        raise ValueError("ranking evidence requires finite numbers")
    for value in explanation.contributions:
        expected = float(value.value) * float(value.weight)
        if not isclose(expected, value.contribution, rel_tol=_TOLERANCE, abs_tol=_TOLERANCE):
            raise ValueError("ranking contribution differs from value times weight")
    total = sum(float(value.contribution) for value in explanation.contributions)
    if not all(isclose(ranked.score, value, rel_tol=_TOLERANCE, abs_tol=_TOLERANCE)
               for value in (explanation.final_score, total)):
        raise ValueError("ranking score differs from its explanation")


def _evaluate(inputs: Iterable[RankingInput], policy: RankingInput, now: datetime) -> tuple[RankingMember, ...]:
    ranker = ReferenceRanker()
    resolved = policy_from_dict(policy.load("policy"))
    members = []
    ids = set()
    for snapshot in inputs:
        candidate = candidate_from_dict(snapshot.load("candidate"))
        if candidate.id in ids:
            raise ValueError("a ranking run cannot contain duplicate candidate IDs")
        ids.add(candidate.id)
        ranked = ranker.rank(candidate, resolved, now=now)
        validate_score(ranked)
        members.append(RankingMember(snapshot, ranked))
    if not members:
        raise ValueError("a ranking run must contain at least one candidate")
    return tuple(sorted(members, key=lambda member: (-member.ranked.score, member.ranked.candidate.id)))


def create_ranking_run(candidates: Iterable[Candidate], policy: Policy, *, now: datetime | None = None) -> RankingRun:
    evaluated_at = _utc(now if now is not None else datetime.now(UTC))
    resolved = policy_from_dict(asdict(policy))
    policy_snapshot = RankingInput.freeze("policy", asdict(resolved))
    members = _evaluate((_candidate_snapshot(candidate) for candidate in candidates), policy_snapshot, evaluated_at)
    return RankingRun(uuid4().hex, evaluated_at, ReferenceRanker.name, policy_snapshot, members)


def replay_ranking_run(run: RankingRun) -> tuple[RankedCandidate, ...]:
    if run.ranker != ReferenceRanker.name or run.context_version is not None or run.context_sha256 is not None:
        raise ValueError("the reference ranker cannot replay an unknown ranker or context")
    for member in run.members:
        validate_score(member.ranked)
    observed = _evaluate((member.snapshot for member in run.members), run.policy, _utc(run.evaluated_at))
    if [ranked_to_dict(value.ranked) for value in observed] != [
        ranked_to_dict(member.ranked) for member in run.members
    ]:
        raise ValueError("ranking run does not reproduce its retained ordering and explanation")
    return tuple(item.ranked for item in observed)


def ranking_run_to_dict(run: RankingRun) -> dict:
    return {
        "id": run.id,
        "evaluated_at": run.evaluated_at.isoformat(),
        "ranker": run.ranker,
        "policy_version": run.policy.load("policy")["version"],
        "policy_sha256": run.policy.sha256,
        "context_version": run.context_version,
        "context_sha256": run.context_sha256,
        "candidates": [
            {**ranked_to_dict(member.ranked), "position": index,
             "candidate_input_sha256": member.snapshot.sha256}
            for index, member in enumerate(run.members, 1)
        ],
    }
