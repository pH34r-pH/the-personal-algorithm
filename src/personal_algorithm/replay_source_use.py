"""Read Long-Haul policy decisions for independently reviewed replay sources."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime

REPLAY_PURPOSE = "personal-algorithm-preference-replay"
_ACTIONS = {"index", "ai-augment", "evaluate", "derive", "ai-training", "publish"}
_STATUSES = ("ALLOW", "DENY", "REQUIRE_CONSENT", "REQUIRE_REVIEW", "UNKNOWN", "CONFLICT",
             "EXPIRED", "RETRACTED", "UNAVAILABLE")
_TRUST_CLASSES = ("external-source", "repository-reference", "accepted-memory", "reviewed-task",
                  "generated-content", "evaluator-evidence", "authoritative-control")
_DATA_CLASSES = ("public", "owner-private", "restricted")
_FIELDS = {"kind", "source", "source_id", "input_sha256", "trust_class", "data_class",
           "rights_reference", "fresh_until", "policy_fingerprint", "decisions"}
_DIGEST = r"[0-9a-f]{64}"


@dataclass(frozen=True, slots=True)
class ReplaySourceInput:
    kind: str
    source: str
    source_id: str
    input_sha256: str

    @property
    def resource_id(self) -> str:
        return f"urn:personal-algorithm:replay:{self.kind}:sha256:{self.input_sha256}"


def _text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _matches(value, pattern: str) -> bool:
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def _source_time(value) -> datetime:
    try:
        if isinstance(value, str):
            instant = datetime.fromisoformat(value)
            if instant.utcoffset() is not None:
                return instant.astimezone(UTC)
    except (ValueError, OverflowError):
        pass
    raise ValueError("Source-use times must include a timezone")


def _request_fingerprint(request: dict) -> str:
    # Long-Haul ResourceUseRequest v1 omits an empty known-absent context.
    canonical = dict(request)
    if not canonical.get("known_absent_context"):
        canonical.pop("known_absent_context", None)
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return "resource-use-v1:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _check_decision(action, record, member, policy, evaluator, caller, when):
    if not isinstance(record, dict) or set(record) != {"request", "decision"}:
        raise ValueError("Invalid source-use decision envelope")
    request, decision = record["request"], record["decision"]
    if not isinstance(request, dict) or not isinstance(decision, dict):
        raise TypeError("Source-use request and decision must be JSON objects")
    observed_context = (
        request.get("action"), request.get("resource_id"),
        request.get("caller_id"), _source_time(request.get("now")),
    )
    if observed_context != (action, member.resource_id, caller, when) or not _text(request.get("request_id")):
        raise ValueError("Source-use request differs from the reviewed replay action and context")
    expected_decision = {
        "resource_id": member.resource_id, "request_id": request["request_id"],
        "request_fingerprint": _request_fingerprint(request), "policy_fingerprint": policy,
        "evaluator_implementation": evaluator,
    }
    if (any(decision.get(key) != value for key, value in expected_decision.items())
            or not _text(decision.get("policy_id")) or _source_time(decision.get("evaluated_at")) != when
            or decision.get("status") not in _STATUSES):
        raise ValueError("Source-use decision differs from the exact request, policy, or evaluator")
    return request, decision


def _allows_private_use(request, decision, purpose) -> bool:
    permissions = decision.get("matched_permissions")
    return (
        request.get("purpose") == purpose
        and request.get("audience_id") == "private"
        and decision["status"] == "ALLOW"
        and decision.get("unsatisfied_duties") == []
        and decision.get("matched_prohibitions") == []
        and isinstance(permissions, list) and bool(permissions) and all(map(_text, permissions))
    )


def _required_decisions(value, action):
    if not isinstance(value, dict) or not value.keys() <= _ACTIONS or action not in value:
        label = "evaluation" if action == "evaluate" else action
        raise ValueError(f"Replay source-use requires an independent {label} decision")
    return value


def _source_row(row, remaining: set[ReplaySourceInput], evaluator, caller, when, action, purpose) -> None:
    if not isinstance(row, dict) or set(row) != _FIELDS:
        raise ValueError("Invalid replay source-use row")
    if not all(_text(row[key]) for key in ("kind", "source", "source_id", "rights_reference")):
        raise ValueError("Replay source-use row lacks exact source identity or reviewed rights")
    member = ReplaySourceInput(row["kind"], row["source"], row["source_id"], row["input_sha256"])
    if not _matches(member.input_sha256, _DIGEST) or member not in remaining:
        raise ValueError("Replay source-use row does not name a unique frozen input")
    if row["trust_class"] not in _TRUST_CLASSES or row["data_class"] not in _DATA_CLASSES:
        raise ValueError("Unknown source trust or data class")
    if _source_time(row["fresh_until"]) <= when:
        raise ValueError("Replay source-use review has expired")
    policy = row["policy_fingerprint"]
    if not _matches(policy, "resource-policy-v1:" + _DIGEST):
        raise ValueError("Replay source-use row requires an exact policy fingerprint")
    decisions = _required_decisions(row["decisions"], action)
    for declared_action, receipt in decisions.items():
        request, decision = _check_decision(declared_action, receipt, member, policy, evaluator, caller, when)
        if declared_action == action and not _allows_private_use(request, decision, purpose):
            raise ValueError("Source-use policy must explicitly allow the reviewed private purpose")
    remaining.remove(member)


def verify_replay_source_use(
    manifest: dict, members: set[ReplaySourceInput], *, when: datetime,
    action: str = "evaluate", purpose: str = REPLAY_PURPOSE,
) -> None:
    """Check exact source coverage and decisions from a pinned trusted review record."""
    fields = {"schema_version", "evaluator_implementation", "caller_id", "sources"}
    if (
        set(manifest) != fields or type(manifest["schema_version"]) is not int
        or manifest["schema_version"] != 1 or not isinstance(manifest["sources"], list)
    ):
        raise ValueError("Invalid replay source-use manifest")
    evaluator, caller = manifest["evaluator_implementation"], manifest["caller_id"]
    if not _matches(evaluator, "resource-policy-evaluator-v1:sha256:" + _DIGEST) or not _text(caller):
        raise ValueError("Replay source-use requires a reviewed evaluator and caller identity")
    remaining = set(members)
    for row in manifest["sources"]:
        _source_row(row, remaining, evaluator, caller, when, action, purpose)
    if remaining:
        raise ValueError("Replay source-use manifest does not cover every frozen input")
