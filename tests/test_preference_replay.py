import hashlib
import json
import sys
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime

import pytest

from personal_algorithm import preference_replay
from personal_algorithm.ranking_runs import (
    RankingInput,
    candidate_snapshot,
    create_ranking_run,
    ranking_run_to_dict,
)
from personal_algorithm.replay_source_use import REPLAY_PURPOSE, ReplaySourceInput
from personal_algorithm.serialization import candidate_from_dict, policy_from_dict
from personal_algorithm.store import Store

NOW = "2026-01-02T00:00:00+00:00"
EVENT_TIME = "2026-01-01T00:00:00+00:00"
PRIVATE_MARKER = "synthetic-private-metadata"


@pytest.fixture
def frozen():
    policy = {"version": "reviewed", "weights": {"topic_affinity": 1.0},
              "topic_affinity": {"databases": -0.1, "systems": 0.2}, "source_affinity": {}}
    policy_sha = RankingInput.freeze("policy", asdict(policy_from_dict(policy))).sha256
    candidates = [
        {"id": identifier, "source": "fixture", "source_id": identifier,
         "canonical_url": f"https://example.invalid/{identifier}", "title": PRIVATE_MARKER,
         "discovered_at": NOW, "content_type": "fixture", "topics": [topic, topic],
         "metadata": {"raw": PRIVATE_MARKER}}
        for identifier, topic in (("a", "databases"), ("b", "systems"))
    ]
    events = [
        {"event_id": identifier, "policy_sha256": policy_sha,
         "origin": "owner_feedback" if explicit else "implicit_telemetry",
         "candidate_id": candidate, "kind": kind, "occurred_at": EVENT_TIME, "explicit": explicit}
        for identifier, candidate, kind, explicit in (
            ("04", "b", "less_like_this", True), ("03", "a", "more_like_this", True),
            ("02", "a", "more_like_this", True), ("05", "b", "more_like_this", False),
            ("06", "b", "saved", True),
        )
    ]
    return {"schema_version": 1, "affinity_step": 0.25, "evaluated_at": NOW,
            "starting_policy": policy, "candidates": candidates, "interactions": events}


def test_canonical_replay_counts_distinct_actions_and_resumes(frozen):
    before = deepcopy(frozen)
    first = preference_replay.replay_preferences(frozen, stop_after=1)
    second = preference_replay.replay_preferences(frozen, checkpoint=first["checkpoint"], stop_after=2)
    assert first["checkpoint"]["topic_deltas"] == {"databases": 1}
    assert second["checkpoint"]["topic_deltas"] == {"databases": 2}
    full = preference_replay.replay_preferences(frozen)
    assert preference_replay.replay_preferences(frozen, checkpoint=second["checkpoint"]) == full
    assert preference_replay.replay_preferences(frozen, checkpoint=full["checkpoint"]) == full
    assert frozen == before
    assert full["checkpoint"]["processed"] == 5
    assert full["checkpoint"]["topic_deltas"] == {"databases": 2, "systems": -1}
    assert full["candidate_policy"]["topic_affinity"] == {"databases": 0.4, "systems": 0.2 - 0.25}
    assert full["baseline_ranking"]["candidates"][0]["candidate_id"] == "b"
    assert full["counterfactual_ranking"]["candidates"][0]["candidate_id"] == "a"
    assert PRIVATE_MARKER not in json.dumps(full)
    assert "example.invalid" not in json.dumps(full)


def test_shuffled_arrivals_and_duplicate_delivery_share_canonical_identity(frozen):
    expected = preference_replay.replay_preferences(frozen)
    duplicate = {**frozen["interactions"][1], "occurred_at": "2025-12-31T16:00:00-08:00"}
    shuffled = {**frozen, "candidates": frozen["candidates"][::-1],
                "interactions": [duplicate, *frozen["interactions"][::-1]]}
    assert preference_replay.replay_preferences(shuffled) == expected
    assert preference_replay.replay_preferences(shuffled, checkpoint=expected["checkpoint"]) == expected


@pytest.mark.parametrize("changes, message", [
    ({"candidate_id": "unlisted"}, "unknown candidate"),
    ({"policy_sha256": "0" * 64}, "unknown policy"),
    ({"occurred_at": "2026-01-01"}, "timezone"),
    ({"occurred_at": "2026-01-03T00:00:00+00:00"}, "after the frozen"),
    ({"origin": "archive_bootstrap"}, "unsupported origin"),
    ({"origin": "provider_sync"}, "unsupported origin"),
    ({"origin": "implicit_telemetry"}, "explicit flag conflicts"),
    ({"explicit": "false"}, "explicit flag conflicts"),
    ({"event_id": ""}, "stable event ID"),
    ({"kind": "unrecognized"}, "supported interaction"),
])
def test_validates_the_entire_input_before_any_checkpoint(frozen, changes, message):
    frozen["interactions"][-1].update(changes)
    with pytest.raises(ValueError, match=message):
        preference_replay.replay_preferences(frozen, stop_after=0)


def test_conflicting_event_id_and_stale_or_tampered_checkpoint_fail(frozen):
    checkpoint = preference_replay.replay_preferences(frozen, stop_after=1)["checkpoint"]
    with pytest.raises(ValueError, match="event ID"):
        conflict = {**frozen["interactions"][0], "kind": "saved"}
        preference_replay.replay_preferences({**frozen, "interactions": [conflict, *frozen["interactions"]]})

    with pytest.raises(ValueError, match="event prefix"):
        preference_replay.replay_preferences(frozen, checkpoint={**checkpoint, "topic_deltas": {"databases": 8}})
    late = dict(frozen["interactions"][0], event_id="01", occurred_at="2025-12-30T00:00:00+00:00")
    revised = {**frozen, "interactions": [*frozen["interactions"], late]}
    with pytest.raises(ValueError, match="differs from the frozen replay"):
        preference_replay.replay_preferences(revised, checkpoint=checkpoint)
    restarted = preference_replay.replay_preferences(revised)
    assert restarted["checkpoint"]["topic_deltas"]["systems"] == -2


def test_comparison_reproduces_in_fresh_sqlite(frozen, tmp_path):
    result = preference_replay.replay_preferences(frozen)
    path = tmp_path / "candidate.sqlite3"
    run = create_ranking_run(
        map(candidate_from_dict, frozen["candidates"]), policy_from_dict(result["candidate_policy"]),
        now=datetime.fromisoformat(NOW),
    )
    Store(path).put_ranking_run(run)
    restored = ranking_run_to_dict(Store(path).get_ranking_run(run.id))
    del restored["id"]
    assert restored == result["counterfactual_ranking"]


def _write_fixture(path, document):
    raw = json.dumps(document).encode()
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def test_cli_checkpoint_restart_after_interruption_is_atomic(frozen, tmp_path, monkeypatch, capsys):
    source, partial, outcome = (tmp_path / name for name in ("input.json", "prefix.json", "result.json"))
    source_sha = _write_fixture(source, frozen)
    command = ["replay", "--input", str(source), "--input-sha256", source_sha]
    monkeypatch.setattr(sys, "argv", [*command, "--output", str(partial), "--stop-after", "2"])
    assert preference_replay.main() == 0
    pinned = hashlib.sha256(partial.read_bytes()).hexdigest()
    args = [*command, "--resume", str(partial), "--resume-sha256", pinned, "--output", str(outcome)]

    def interrupted(*_):
        raise OSError("Synthetic interruption at candidate publication")

    with monkeypatch.context() as fault:
        fault.setattr(preference_replay.os, "link", interrupted)
        fault.setattr(sys, "argv", args)
        assert preference_replay.main() == 1
        assert not outcome.exists()
        assert not list(tmp_path.glob(".preference-replay-*"))
    monkeypatch.setattr(sys, "argv", args)
    assert preference_replay.main() == 0
    raw = outcome.read_bytes()
    assert preference_replay.main() == 1
    assert outcome.read_bytes() == raw
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_sha
    assert hashlib.sha256(partial.read_bytes()).hexdigest() == pinned
    assert json.loads(raw) == {
        **preference_replay.replay_preferences(frozen), "frozen_input_file_sha256": source_sha,
    }
    captured = capsys.readouterr()
    assert PRIVATE_MARKER not in captured.out + captured.err


def test_cli_rejects_wrong_pin_and_corrupt_json_without_writing(frozen, tmp_path, monkeypatch):
    source, outcome = tmp_path / "input.json", tmp_path / "result.json"
    expected = _write_fixture(source, frozen)
    prefix = ["replay", "--input", str(source), "--output", str(outcome), "--input-sha256"]
    monkeypatch.setattr(sys, "argv", [*prefix, "0" * 64])
    assert preference_replay.main() == 1
    source.write_bytes(source.read_bytes()[:-1])
    monkeypatch.setattr(sys, "argv", [*prefix, hashlib.sha256(source.read_bytes()).hexdigest()])
    assert preference_replay.main() == 1
    assert hashlib.sha256(source.read_bytes()).hexdigest() != expected
    assert not outcome.exists()


EVALUATOR = "resource-policy-evaluator-v1:sha256:" + "a" * 64
POLICY = "resource-policy-v1:" + "b" * 64
CALLER = "fixture:reviewed-owner"


def source_decision(member, action, status):
    context = {
        "request_id": f"fixture:{action}", "resource_id": member.resource_id,
        "action": action, "purpose": REPLAY_PURPOSE, "caller_id": CALLER,
        "crew_id": None, "mission_id": None, "contract_id": None, "repository_id": None,
        "audience_id": "private", "fulfilled_duties": [], "now": "2026-01-02T00:00:00Z",
    }
    encoded = json.dumps(context, sort_keys=True, separators=(",", ":")).encode()
    decision = {
        "request_id": context["request_id"], "resource_id": member.resource_id, "status": status,
        "request_fingerprint": "resource-use-v1:" + hashlib.sha256(encoded).hexdigest(),
        "evaluated_at": NOW, "policy_fingerprint": POLICY,
        "policy_id": "fixture:source-policy", "evaluator_implementation": EVALUATOR,
        "matched_permissions": ["explicit-permission"] if status == "ALLOW" else [],
        "matched_prohibitions": ["explicit-denial"] if status == "DENY" else [],
        "unsatisfied_duties": [],
    }
    return {"request": context, "decision": decision}


@pytest.fixture
def reviewed_sources(frozen):
    candidates = [candidate_from_dict(value) for value in frozen["candidates"]]
    members = [
        ReplaySourceInput("candidate", candidate.source, candidate.source_id, candidate_snapshot(candidate).sha256)
        for candidate in candidates
    ]
    for event in frozen["interactions"]:
        encoded = (json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n").encode()
        members.append(ReplaySourceInput("interaction", event["origin"], event["event_id"],
                                         hashlib.sha256(encoded).hexdigest()))
    return {
        "schema_version": 1, "evaluator_implementation": EVALUATOR, "caller_id": CALLER,
        "sources": [
            {
                **asdict(member), "trust_class": "external-source", "data_class": "owner-private",
                "rights_reference": "fixture:reviewed-owner-consent", "fresh_until": "2026-01-03T00:00:00Z",
                "policy_fingerprint": POLICY,
                "decisions": {
                    action: source_decision(member, action, status)
                    for action, status in (
                        ("index", "ALLOW"), ("evaluate", "ALLOW"), ("ai-augment", "DENY"),
                        ("ai-training", "DENY"), ("publish", "DENY"),
                    )
                },
            }
            for member in members
        ],
    }


def governed_input(frozen, reviewed_sources):
    payload = json.dumps(reviewed_sources, sort_keys=True).encode()
    expected = hashlib.sha256(payload).hexdigest()
    return (
        {**frozen, "schema_version": 2, "source_use_sha256": expected},
        {"source_use": payload, "source_use_sha256": expected},
    )


def test_governed_replay_keeps_actions_independent_and_resumes_exactly(frozen, reviewed_sources):
    document, options = governed_input(frozen, reviewed_sources)
    partial = preference_replay.replay_preferences(document, stop_after=2, **options)
    full = preference_replay.replay_preferences(document, **options)
    assert full["schema_version"] == full["checkpoint"]["schema_version"] == 2
    assert full["source_use_sha256"] == document["source_use_sha256"]
    assert preference_replay.replay_preferences(document, checkpoint=partial["checkpoint"], **options) == full
    assert full["checkpoint"]["topic_deltas"] == {"databases": 2, "systems": -1}
    assert CALLER not in json.dumps(full)
    assert PRIVATE_MARKER not in json.dumps(full)


def test_governed_checkpoint_cannot_resume_under_a_different_source_review(frozen, reviewed_sources):
    document, options = governed_input(frozen, reviewed_sources)
    partial = preference_replay.replay_preferences(document, stop_after=1, **options)
    reviewed_sources["sources"][0]["rights_reference"] = "fixture:successor-review"
    revised, new_options = governed_input(frozen, reviewed_sources)
    with pytest.raises(ValueError, match="Checkpoint differs"):
        preference_replay.replay_preferences(revised, checkpoint=partial["checkpoint"], **new_options)


@pytest.mark.parametrize("change, message", [
    ("missing", "does not cover every"),
    ("duplicate", "unique frozen input"),
    ("wrong_source", "unique frozen input"),
    ("changed_candidate", "unique frozen input"),
    ("changed_feedback", "unique frozen input"),
    ("expired", "review has expired"),
])
def test_governed_replay_rejects_source_mismatch_before_ranking(frozen, reviewed_sources, change, message, monkeypatch):
    rows = reviewed_sources["sources"]
    if change == "missing":
        rows.pop()
    elif change == "duplicate":
        rows.append(rows[0])
    elif change == "wrong_source":
        rows[0]["source_id"] = "different-source"
    elif change == "changed_candidate":
        frozen["candidates"][0]["metadata"]["raw"] = "changed evidence"
    elif change == "changed_feedback":
        frozen["interactions"][0]["kind"] = "saved"
    else:
        rows[0]["fresh_until"] = NOW
    document, options = governed_input(frozen, reviewed_sources)

    def unexpected(*args, **kwargs):
        pytest.fail("Source verification must precede ranking")

    monkeypatch.setattr(preference_replay, "create_ranking_run", unexpected)
    with pytest.raises(ValueError, match=message):
        preference_replay.replay_preferences(document, **options)


@pytest.mark.parametrize("change, message", [
    ("missing", "independent evaluation"),
    ("denied", "explicitly allow"),
    ("wrong_context", "action and context"),
    ("wrong_commitment", "exact request"),
    ("wrong_purpose", "explicitly allow"),
    ("wrong_audience", "explicitly allow"),
    ("wrong_evaluator", "exact request"),
    ("wrong_policy", "exact request"),
    ("outstanding_duty", "explicitly allow"),
])
def test_governed_replay_requires_the_exact_explicit_evaluation_decision(frozen, reviewed_sources, change, message):
    decisions = reviewed_sources["sources"][0]["decisions"]
    approval = decisions["evaluate"]
    if change == "missing":
        del decisions["evaluate"]
    elif change == "denied":
        approval["decision"]["status"] = "DENY"
    elif change == "wrong_context":
        approval["request"]["caller_id"] = "fixture:other-owner"
    elif change == "wrong_commitment":
        approval["request"]["audience_id"] = "public"
    elif change in ("wrong_purpose", "wrong_audience"):
        field = "purpose" if change == "wrong_purpose" else "audience_id"
        approval["request"][field] = "separate-use"
        encoded = json.dumps(approval["request"], sort_keys=True, separators=(",", ":")).encode()
        approval["decision"]["request_fingerprint"] = "resource-use-v1:" + hashlib.sha256(encoded).hexdigest()
    elif change == "wrong_evaluator":
        approval["decision"]["evaluator_implementation"] += "different"
    elif change == "wrong_policy":
        approval["decision"]["policy_fingerprint"] = "resource-policy-v1:" + "f" * 64
    else:
        approval["decision"]["unsatisfied_duties"] = ["prior consent"]
    document, options = governed_input(frozen, reviewed_sources)
    with pytest.raises(ValueError, match=message):
        preference_replay.replay_preferences(document, **options)


def test_governed_cli_requires_both_reviewed_pins(frozen, reviewed_sources, tmp_path, monkeypatch, capsys):
    source, use, output = [tmp_path / name for name in ("input.json", "sources.json", "candidate.json")]
    document, options = governed_input(frozen, reviewed_sources)
    source_pin = _write_fixture(source, document)
    use.write_bytes(options["source_use"])
    command = ["replay", "--input", str(source), "--input-sha256", source_pin, "--output", str(output)]
    monkeypatch.setattr(sys, "argv", command)
    assert preference_replay.main() == 1
    assert not output.exists()
    args = [*command, "--source-use", str(use), "--source-use-sha256", options["source_use_sha256"]]
    use.write_bytes(use.read_bytes() + b" ")
    monkeypatch.setattr(sys, "argv", args)
    assert preference_replay.main() == 1
    assert not output.exists()
    use.write_bytes(options["source_use"])
    assert preference_replay.main() == 0
    observed = json.loads(output.read_bytes())
    assert observed["source_use_sha256"] == options["source_use_sha256"]
    assert observed["frozen_input_file_sha256"] == source_pin
    captured = capsys.readouterr()
    assert PRIVATE_MARKER not in captured.out + captured.err
