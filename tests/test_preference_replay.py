import hashlib
import json
import sys
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime

import pytest

from personal_algorithm import preference_replay
from personal_algorithm.ranking_runs import RankingInput, create_ranking_run, ranking_run_to_dict
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
