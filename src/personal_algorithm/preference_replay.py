"""Replay frozen feedback and compare reference rankings in a local candidate file."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from collections import Counter
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import Candidate, InteractionKind, Policy, finite_number_map
from .ranking_runs import (
    RankingInput,
    RankingRun,
    candidate_snapshot,
    create_ranking_run,
    ranking_run_to_dict,
)
from .replay_source_use import ReplaySourceInput, verify_replay_source_use
from .serialization import candidate_from_dict, interaction_from_dict, policy_from_dict

REDUCER = "explicit-topic-feedback-v1"
INPUT_FIELDS = {"schema_version", "evaluated_at", "starting_policy", "candidates",
                "interactions", "affinity_step"}
INPUT_FIELDS_BY_VERSION = {1: INPUT_FIELDS, 2: INPUT_FIELDS | {"source_use_sha256"}}
EVENT_FIELDS = {"event_id", "policy_sha256", "origin", "candidate_id", "kind",
                "occurred_at", "explicit"}
CHECKPOINT_FIELDS = {"schema_version", "reducer", "input_sha256", "processed", "topic_deltas"}
SIGNALS = {InteractionKind.MORE_LIKE_THIS: 1, InteractionKind.LESS_LIKE_THIS: -1}


def _json(document: dict) -> bytes:
    return (json.dumps(document, sort_keys=True, ensure_ascii=False,
                       separators=(",", ":"), allow_nan=False) + "\n").encode()


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _object(value: Any, keys: set[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError(f"Invalid {label} fields")
    return value


def _unique_fields(pairs: list[tuple[str, Any]]) -> dict:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("Duplicate JSON field in replay input")
    return result


def _pinned_object(raw: bytes, expected: str) -> dict:
    if _digest(raw) != expected:
        raise ValueError("Pinned replay file SHA-256 mismatch")
    document = json.loads(raw, object_pairs_hook=_unique_fields)
    if not isinstance(document, dict):
        raise TypeError("Expected a replay JSON object")
    return document


def _read_pinned(path: Path, expected: str) -> dict:
    return _pinned_object(path.read_bytes(), expected)


def _interaction(row: Any, policy_sha: str, candidate_ids: set[str], when: datetime) -> dict:
    value = _object(row, EVENT_FIELDS, "interaction")
    event_id = value["event_id"]
    if not isinstance(event_id, str) or not event_id.strip():
        raise ValueError("Interaction requires a stable event ID")
    if value["policy_sha256"] != policy_sha:
        raise ValueError("Interaction references an unknown policy")
    origin = value["origin"]
    if origin not in ("owner_feedback", "implicit_telemetry"):
        raise ValueError("Interaction has an unsupported origin")
    if type(value["explicit"]) is not bool or value["explicit"] != (origin == "owner_feedback"):
        raise ValueError("Interaction explicit flag conflicts with its origin")
    event = interaction_from_dict(value)
    if event.candidate_id not in candidate_ids:
        raise ValueError("Interaction references an unknown candidate")
    occurred = event.occurred_at.astimezone(UTC)
    if occurred > when:
        raise ValueError("Interaction occurs after the frozen evaluation time")
    return {**value, "occurred_at": occurred.isoformat()}


def _ordered_events(rows: list, policy_sha: str, candidate_ids: set[str], when: datetime) -> tuple[dict, ...]:
    if not isinstance(rows, list):
        raise TypeError("Expected an interaction list")
    by_id: dict[str, dict] = {}
    for row in rows:
        event = _interaction(row, policy_sha, candidate_ids, when)
        existing = by_id.setdefault(event["event_id"], event)
        if event != existing:
            raise ValueError("Conflicting reuse of a replay event ID")
    return tuple(sorted(by_id.values(), key=lambda row: (
        datetime.fromisoformat(row["occurred_at"]), row["event_id"],
    )))


@dataclass(frozen=True, slots=True)
class _FrozenReplay:
    starting_policy: Policy
    baseline: RankingRun
    candidates: tuple[Candidate, ...]
    events: tuple[dict, ...]
    affinity_step: float
    input_sha256: str
    schema_version: int
    source_use_sha256: str | None


def _review_source_inputs(bound_pin, source_use, source_use_sha256, candidates, events, when) -> str:
    if source_use is None or source_use_sha256 is None or bound_pin != source_use_sha256:
        raise ValueError("Governed replay requires the independently reviewed source-use pin")
    reviewed = _pinned_object(source_use, source_use_sha256)
    members = {
        ReplaySourceInput("candidate", candidate.source, candidate.source_id, snapshot.sha256)
        for candidate, snapshot in candidates
    }
    members.update(ReplaySourceInput("interaction", event["origin"], event["event_id"], _digest(_json(event)))
                   for event in events)
    verify_replay_source_use(reviewed, members, when=when)
    return source_use_sha256


def _prepare_replay(document: dict, source_use: bytes | None, source_use_sha256: str | None) -> _FrozenReplay:
    version = document.get("schema_version") if isinstance(document, dict) else None
    if type(version) is not int or version not in INPUT_FIELDS_BY_VERSION:
        raise ValueError("Unsupported frozen replay input version")
    data = _object(document, INPUT_FIELDS_BY_VERSION[version], "frozen replay input")
    value = data["evaluated_at"]
    if not isinstance(value, str) or (when := datetime.fromisoformat(value)).utcoffset() is None:
        raise ValueError("Frozen evaluation time must include a timezone")
    when = when.astimezone(UTC)
    policy = policy_from_dict(data["starting_policy"])
    policy_sha = RankingInput.freeze("policy", asdict(policy)).sha256
    if not isinstance(data["candidates"], list):
        raise TypeError("Expected a frozen candidate list")
    snapshots = tuple(candidate_snapshot(candidate_from_dict(row)) for row in data["candidates"])
    candidates = tuple(candidate_from_dict(snapshot.load("candidate")) for snapshot in snapshots)
    events = _ordered_events(data["interactions"], policy_sha, {candidate.id for candidate in candidates}, when)
    step = finite_number_map({"step": data["affinity_step"]}, "affinity_step")["step"]
    if step <= 0:
        raise ValueError("Affinity step must be positive")
    binding = {}
    if version == 2:
        binding["source_use_sha256"] = _review_source_inputs(
            data["source_use_sha256"], source_use, source_use_sha256,
            zip(candidates, snapshots, strict=True), events, when,
        )
    elif source_use is not None or source_use_sha256 is not None:
        raise ValueError("Source-use verification requires the governed replay format")
    baseline = create_ranking_run(candidates, policy, now=when)
    identity = _digest(_json({
        "reducer": REDUCER, "evaluated_at": when.isoformat(),
        "starting_policy_sha256": policy_sha,
        "candidates": sorted(snapshot.sha256 for snapshot in snapshots),
        "interactions": events, "affinity_step": step,
        **binding,
    }))
    return _FrozenReplay(policy, baseline, candidates, events, step, identity, version, source_use_sha256)


def _apply(replay: _FrozenReplay, start: int, end: int, prior: dict) -> dict:
    topics = {candidate.id: set(candidate.topics) for candidate in replay.candidates}
    counts = Counter(prior)
    for event in replay.events[start:end]:
        signal = SIGNALS.get(event["kind"], 0)
        if event["explicit"] and signal:
            for topic in topics[event["candidate_id"]]:
                counts[topic] += signal
    return {topic: count for topic, count in counts.items() if count}


def _resume(replay: _FrozenReplay, checkpoint: dict | None) -> tuple[int, dict]:
    if checkpoint is None:
        return 0, {}
    data = _object(checkpoint, CHECKPOINT_FIELDS, "checkpoint")
    position = data["processed"]
    if (type(data["schema_version"]) is not int or data["schema_version"] != replay.schema_version
            or data["reducer"] != REDUCER or data["input_sha256"] != replay.input_sha256
            or type(position) is not int or not 0 <= position <= len(replay.events)):
        raise ValueError("Checkpoint differs from the frozen replay")
    expected = _apply(replay, 0, position, {})
    if _json({"topic_deltas": expected}) != _json({"topic_deltas": data["topic_deltas"]}):
        raise ValueError("Checkpoint state differs from the frozen event prefix")
    return position, expected


def _ranking(run: RankingRun) -> dict:
    fields = ranking_run_to_dict(run)
    del fields["id"]  # The input fingerprint supplies the stable replay identity.
    return fields


def replay_preferences(document: dict, *, checkpoint: dict | None = None,
                       stop_after: int | None = None, source_use: bytes | None = None,
                       source_use_sha256: str | None = None) -> dict:
    """Return one deterministic snapshot after validating the complete frozen input."""
    replay = _prepare_replay(document, source_use, source_use_sha256)
    start, prior = _resume(replay, checkpoint)
    total = len(replay.events)
    end = total if stop_after is None else stop_after
    if type(end) is not int or not start <= end <= total:
        raise ValueError("Stop position must be within the remaining frozen replay")
    counts = _apply(replay, start, end, prior)
    state = {"schema_version": replay.schema_version, "reducer": REDUCER, "input_sha256": replay.input_sha256,
             "processed": end, "topic_deltas": counts}
    envelope = {"schema_version": replay.schema_version, "checkpoint": state}
    if replay.source_use_sha256 is not None:
        envelope["source_use_sha256"] = replay.source_use_sha256
    if end != total:
        return {**envelope, "status": "checkpoint"}
    initial = replay.starting_policy
    affinity = dict(initial.topic_affinity)
    for topic, count in counts.items():
        affinity[topic] = affinity.get(topic, 0.0) + count * replay.affinity_step
    candidate = replace(initial, version=f"{initial.version}+replay-{replay.input_sha256}",
                        topic_affinity=affinity)
    counterfactual = create_ranking_run(replay.candidates, candidate, now=replay.baseline.evaluated_at)
    return {
        **envelope, "status": "candidate",
        "candidate_policy": asdict(candidate),
        "baseline_ranking": _ranking(replay.baseline),
        "counterfactual_ranking": _ranking(counterfactual),
    }


def _write_new(path: Path, data: bytes) -> None:
    descriptor, name = tempfile.mkstemp(prefix=".preference-replay-", dir=path.parent)
    staged = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.link(staged, path)
    finally:
        staged.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--input-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume", type=Path)
    parser.add_argument("--resume-sha256")
    parser.add_argument("--stop-after", type=int)
    parser.add_argument("--source-use", type=Path)
    parser.add_argument("--source-use-sha256")
    args = parser.parse_args()
    if (args.resume is None) != (args.resume_sha256 is None):
        parser.error("--resume and --resume-sha256 must be supplied together")
    if (args.source_use is None) != (args.source_use_sha256 is None):
        parser.error("--source-use and --source-use-sha256 must be supplied together")
    try:
        source = _read_pinned(args.input, args.input_sha256)
        checkpoint = None
        if args.resume is not None:
            saved = _read_pinned(args.resume, args.resume_sha256)
            checkpoint = _object(saved.get("checkpoint"), CHECKPOINT_FIELDS, "checkpoint")
        result = replay_preferences(
            source, checkpoint=checkpoint, stop_after=args.stop_after,
            source_use=args.source_use.read_bytes() if args.source_use is not None else None,
            source_use_sha256=args.source_use_sha256,
        )
        payload = _json({**result, "frozen_input_file_sha256": args.input_sha256})
        _write_new(args.output, payload)
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        print("Replay rejected. Check the frozen input, source review, checkpoint, and new output path.",
              file=sys.stderr)
        return 1
    print(f"Private replay {result['status']} SHA-256 {_digest(payload)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
