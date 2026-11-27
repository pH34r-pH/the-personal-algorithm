"""Record an explicit local ranking-policy promotion or rollback."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from sqlite3 import DatabaseError

from .config import load_config
from .preference_replay import _digest, _json, _object
from .ranking_runs import RankingInput
from .replay_outcomes import PinnedFile, compare_outcomes
from .replay_source_use import (
    _DIGEST,
    ReplaySourceInput,
    _matches,
    _source_time,
    _text,
    verify_replay_source_use,
)
from .serialization import policy_from_dict
from .store import Store

ACTIVATION_PURPOSE = "personal-algorithm-private-ranking-policy"
COMMON = {
    "schema_version", "action", "operator_id", "decided_at", "instance_config_sha256", "database",
    "previous_approval_sha256", "previous_policy_sha256", "policy_sha256",
}
PROMOTION = {"candidate_sha256", "comparison_sha256", "activation_source_use_sha256"}
ROLLBACK = {"restore_approval_sha256"}
COMPARISON_FILES = ("input", "source-use", "candidate", "outcomes", "outcome-source-use")


def load_approval(pinned: PinnedFile, action: str, now: datetime) -> dict:
    fields = PROMOTION if action == "promote" else ROLLBACK
    approval = _object(pinned.read(), COMMON | fields, "operator approval")
    if (type(approval["schema_version"]) is not int or approval["schema_version"] != 1
            or approval["action"] != action or not _text(approval["operator_id"])
            or _source_time(approval["decided_at"]) > now):
        raise ValueError("Invalid operator identity, action, or decision time")
    for key in COMMON | fields:
        if key in ("previous_approval_sha256", "restore_approval_sha256") and approval[key] is None:
            continue
        if key.endswith("_sha256") and not _matches(approval[key], _DIGEST):
            raise ValueError("Operator approval requires exact digests")
    return approval


def _check_activation_source_use(approval, frozen_review, activation_review, now):
    reviewed = activation_review.read()
    if reviewed.get("caller_id") != approval["operator_id"]:
        raise ValueError("Activation source review does not name the approving operator")
    members = {
        ReplaySourceInput(*(row[key] for key in ("kind", "source", "source_id", "input_sha256")))
        for row in frozen_review.read()["sources"]
    }
    verify_replay_source_use(
        reviewed, members, when=_source_time(approval["decided_at"]),
        action="derive", purpose=ACTIVATION_PURPOSE,
    )
    expiry = min(_source_time(row["fresh_until"]) for row in reviewed["sources"])
    if expiry <= now:
        raise ValueError("Activation source review has expired")
    return expiry.isoformat()


def reviewed_target(approval, files, now) -> tuple[RankingInput, str]:
    for kind, field in (("candidate", "candidate_sha256"), ("comparison", "comparison_sha256"),
                        ("activation-source-use", "activation_source_use_sha256")):
        if files[kind].sha256 != approval[field]:
            raise ValueError("Promotion evidence differs from the operator approval")
    comparison = compare_outcomes(
        *(files[key] for key in COMPARISON_FILES), forecasts=files.get("forecasts"),
    )
    payload = _json(comparison)
    comparison_file = files["comparison"]
    if (_digest(payload) != comparison_file.sha256 or comparison_file.path.read_bytes() != payload
            or not comparison["judged_candidates"]
            or _source_time(comparison["observed_through"]) > _source_time(approval["decided_at"])):
        raise ValueError("Promotion requires the exact reproduced comparison with observed outcomes before approval")
    candidate = files["candidate"].read()
    if candidate["baseline_ranking"]["policy_sha256"] != approval["previous_policy_sha256"]:
        raise ValueError("Candidate starting policy differs from the approved baseline")
    policy = policy_from_dict(candidate["candidate_policy"])
    snapshot = RankingInput.freeze("policy", asdict(policy))
    if snapshot.sha256 != approval["policy_sha256"]:
        raise ValueError("Approved target differs from the exact replay policy")
    expiry = _check_activation_source_use(approval, files["source-use"], files["activation-source-use"], now)
    return snapshot, expiry


def _pinned(value) -> PinnedFile:
    return PinnedFile(Path(value[0]), value[1])


def _change(config_path, command, approval_file, files, now) -> dict:
    approval = load_approval(approval_file, command, now)
    config = load_config(config_path, expected_sha256=approval["instance_config_sha256"])
    database = Path(config.database).resolve()
    if database != Path(approval["database"]):
        raise ValueError("Operator approval does not identify the local database")
    promoted, valid_until = reviewed_target(approval, files, now) if command == "promote" else (None, None)
    store = Store(database)
    try:
        changed = store.record_ranking_policy_change(
            config.policy, approval_file.sha256, approval, promoted=promoted, valid_until=valid_until, now=now,
        )
    finally:
        store.connection.close()
    return {"approval_sha256": approval_file.sha256, "policy_sha256": approval["policy_sha256"],
            "result": "recorded" if changed else "already-recorded"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    promote = commands.add_parser("promote")
    rollback = commands.add_parser("rollback")
    for command in (promote, rollback):
        command.add_argument("--approval", nargs=2, metavar=("FILE", "SHA256"), required=True)
    promotion_inputs = (*COMPARISON_FILES, "forecasts", "comparison", "activation-source-use")
    for name in promotion_inputs:
        promote.add_argument(f"--{name}", nargs=2, metavar=("FILE", "SHA256"), required=name != "forecasts")
    args = parser.parse_args()
    try:
        if args.command == "status":
            config = load_config(args.config)
            store = Store(config.database)
            try:
                result = store.ranking_policy_status(config.policy)
            finally:
                store.connection.close()
        else:
            files = {name: _pinned(value) for name in promotion_inputs
                     if (value := getattr(args, name.replace("-", "_"), None)) is not None}
            result = _change(args.config, args.command, _pinned(args.approval), files, datetime.now(UTC))
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError, DatabaseError):
        print("Policy change rejected. Check local configuration, operator approval, and reviewed evidence.",
              file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
