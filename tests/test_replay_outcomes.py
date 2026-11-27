import json
import sys
from dataclasses import asdict

import pytest
import test_preference_replay as fixtures
from test_preference_replay import (
    CALLER,
    EVALUATOR,
    NOW,
    POLICY,
    PRIVATE_MARKER,
    governed_input,
    source_decision,
)

from personal_algorithm import preference_replay, replay_outcomes
from personal_algorithm.replay_source_use import ReplaySourceInput

frozen = fixtures.frozen
reviewed_sources = fixtures.reviewed_sources

THROUGH = "2026-01-06T00:00:00+00:00"
OBSERVED = "2026-01-05T00:00:00+00:00"
FLAGS = ("input", "source-use", "candidate", "outcomes", "outcome-source-use", "forecasts")


def pin(path, raw):
    path.write_bytes(raw)
    return replay_outcomes.PinnedFile(path, preference_replay._digest(raw))


def review(rows, *, status="ALLOW"):
    records = []
    for row in rows:
        raw = preference_replay._json(row)
        member = ReplaySourceInput("outcome", row["source"], row["source_id"], preference_replay._digest(raw))
        records.append({
            **asdict(member), "trust_class": "evaluator-evidence", "data_class": "owner-private",
            "rights_reference": PRIVATE_MARKER, "fresh_until": "2026-01-07T00:00:00Z",
            "policy_fingerprint": POLICY,
            "decisions": {"evaluate": source_decision(member, "evaluate", status, when=THROUGH)},
        })
    return {"schema_version": 1, "evaluator_implementation": EVALUATOR, "caller_id": CALLER, "sources": records}


@pytest.fixture
def comparison(frozen, reviewed_sources, tmp_path):
    source, options = governed_input(frozen, reviewed_sources)
    replay_input = pin(tmp_path / "replay.json", preference_replay._json(source))
    replay_review = pin(tmp_path / "replay-review.json", options["source_use"])
    output = preference_replay.replay_preferences(source, **options)
    candidate = pin(tmp_path / "candidate.json", preference_replay._json({
        **output, "frozen_input_file_sha256": replay_input.sha256,
    }))
    binding = {"schema_version": 1, "frozen_input_sha256": replay_input.sha256, "candidate_sha256": candidate.sha256}
    outcomes, forecasts = [], []
    for value in output["baseline_ranking"]["candidates"]:
        key = value["candidate_id"]
        identity = {"candidate_id": key, "candidate_input_sha256": value["candidate_input_sha256"]}
        outcomes.append({
            **identity, "source": "private-fixture-source", "source_id": f"private-observed-{key}",
            "observed_at": OBSERVED, "relevant": key == "a",
        })
        forecasts.append({**identity, "baseline": 0.25 if key == "a" else 0.75,
                          "counterfactual": 0.75 if key == "a" else 0.25})
    return {
        "paths": (replay_input, replay_review, candidate), "binding": binding,
        "outcomes": outcomes, "forecasts": {"frozen_at": NOW, "method_sha256": "d" * 64, "predictions": forecasts},
    }


def inputs(fixture, tmp_path, *, status="ALLOW"):
    rows, binding = fixture["outcomes"], fixture["binding"]
    outcome_review = pin(tmp_path / "outcome-review.json", preference_replay._json(review(rows, status=status)))
    packet = pin(tmp_path / "outcomes.json", preference_replay._json({
        **binding, "observed_through": THROUGH, "source_use_sha256": outcome_review.sha256, "outcomes": rows,
    }))
    forecasts = pin(tmp_path / "forecasts.json", preference_replay._json({**binding, **fixture["forecasts"]}))
    return (*fixture["paths"], packet, outcome_review, forecasts)


def cli_args(files, path):
    args = ["replay_outcomes"]
    for flag, pinned in zip(FLAGS, files, strict=True):
        args.extend((f"--{flag}", str(pinned.path), pinned.sha256))
    return [*args, "--output", str(path)]


def test_full_observed_slate_scores_regret_and_separately_frozen_probabilities(comparison, tmp_path, monkeypatch, capsys):
    files = inputs(comparison, tmp_path)
    path = tmp_path / "metrics.json"
    monkeypatch.setattr(sys, "argv", cli_args(files, path))
    assert replay_outcomes.main() == 0
    original = path.read_bytes()
    result = json.loads(original)
    expected = replay_outcomes.compare_outcomes(*files)
    assert result == expected
    assert result["judged_candidates"] == result["candidate_count"] == 2
    baseline, counterfactual = (result["metrics"][name] for name in ("baseline", "counterfactual"))
    assert (baseline["correct_judged_pairs"], counterfactual["correct_judged_pairs"]) == (0, 1)
    assert baseline["judged_positive_negative_pairs"] == counterfactual["judged_positive_negative_pairs"] == 1
    assert (baseline["top_rank_outcome"], counterfactual["top_rank_outcome"]) == (False, True)
    assert (baseline["top_rank_regret"], counterfactual["top_rank_regret"]) == (1, 0)
    assert baseline["calibration"]["brier_score"] == {"numerator": 9, "denominator": 16}
    assert counterfactual["calibration"]["brier_score"] == {"numerator": 1, "denominator": 16}
    assert [row["observed_rate"] for row in counterfactual["calibration"]["bins"]] == [
        {"numerator": 0, "denominator": 1}, {"numerator": 1, "denominator": 1},
    ]
    stdout = capsys.readouterr().out
    assert stdout.strip().endswith(preference_replay._digest(original))
    for private in (PRIVATE_MARKER, CALLER, "candidate_id", "private-observed", "example.invalid"):
        assert private not in stdout + original.decode()
    assert replay_outcomes.main() == 1
    assert path.read_bytes() == original


def test_partial_observed_slate_keeps_untested_outcomes_and_regret_unknown(comparison, tmp_path):
    comparison["outcomes"] = [row for row in comparison["outcomes"] if row["candidate_id"] == "a"]
    files = inputs(comparison, tmp_path)
    report = replay_outcomes.compare_outcomes(*files[:-1])
    assert (report["judged_candidates"], report["candidate_count"]) == (1, 2)
    assert report["metrics"]["baseline"]["top_rank_outcome"] is None
    assert report["metrics"]["counterfactual"]["top_rank_outcome"] is True
    for metrics in report["metrics"].values():
        assert metrics["top_rank_regret"] is metrics["calibration"] is None
        assert metrics["judged_positive_negative_pairs"] == 0


def test_rejects_ineligible_outcomes_forecasts_and_changed_candidate(comparison, tmp_path):
    files = inputs(comparison, tmp_path, status="DENY")
    with pytest.raises(ValueError, match="explicitly allow"):
        replay_outcomes.compare_outcomes(*files)
    original = comparison["outcomes"][0]
    for fields, message in (
        ({"observed_at": NOW}, "must follow"),
        ({"candidate_input_sha256": "0" * 64}, "unique frozen candidate"),
        ({"relevant": 1}, "binary label"),
    ):
        comparison["outcomes"][0] = original | fields
        with pytest.raises(ValueError, match=message):
            replay_outcomes.compare_outcomes(*inputs(comparison, tmp_path))
    comparison["outcomes"][0] = original
    comparison["forecasts"]["frozen_at"] = OBSERVED
    with pytest.raises(ValueError, match="before observed outcomes"):
        replay_outcomes.compare_outcomes(*inputs(comparison, tmp_path))
    comparison["forecasts"]["frozen_at"] = NOW
    comparison["forecasts"]["predictions"][0]["baseline"] = 1.01
    with pytest.raises(ValueError, match="probabilities"):
        replay_outcomes.compare_outcomes(*inputs(comparison, tmp_path))
    files = inputs(comparison, tmp_path)
    candidate = files[2].path
    candidate.write_bytes(candidate.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="pinned bytes"):
        replay_outcomes.compare_outcomes(*files)
