"""Compare frozen preference rankings against reviewed, later outcomes."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from .preference_replay import (
    _digest,
    _json,
    _object,
    _pinned_object,
    _write_new,
    replay_preferences,
)
from .replay_source_use import (
    _DIGEST,
    ReplaySourceInput,
    _matches,
    _source_time,
    _text,
    verify_replay_source_use,
)

IDENTITY = {"schema_version", "frozen_input_sha256", "candidate_sha256"}
OUTCOME_FIELDS = {
    "candidate_id", "candidate_input_sha256", "source", "source_id", "observed_at", "relevant",
}
FORECAST_FIELDS = {"candidate_id", "candidate_input_sha256", "baseline", "counterfactual"}
TREATMENTS = ("baseline", "counterfactual")


@dataclass(frozen=True, slots=True)
class PinnedFile:
    path: Path
    sha256: str

    def read(self) -> dict:
        return _pinned_object(self.path.read_bytes(), self.sha256)


def _packet(binding, pinned, fields, label) -> dict:
    packet = _object(pinned.read(), IDENTITY | fields, label)
    if type(packet["schema_version"]) is not int or any(packet[key] != value for key, value in binding.items()):
        raise ValueError(f"{label} does not bind the exact replay input and candidate")
    return packet


def _outcome_rows(rows, population, evaluated_at, through):
    if not isinstance(rows, list):
        raise TypeError("Expected an outcome list")
    observations, members, identities = {}, set(), set()
    for item in rows:
        row = _object(item, OUTCOME_FIELDS, "outcome")
        key = row["candidate_id"]
        if (not _text(key) or key in observations or key not in population
                or row["candidate_input_sha256"] != population[key]):
            raise ValueError("Outcome does not identify a unique frozen candidate")
        if type(row["relevant"]) is not bool or not all(_text(row[field]) for field in ("source", "source_id")):
            raise ValueError("Outcome requires an observed binary label and exact source identity")
        identity = row["source"], row["source_id"]
        if identity in identities:
            raise ValueError("Outcome source identity is reused")
        identities.add(identity)
        observed = _source_time(row["observed_at"])
        if not evaluated_at < observed <= through:
            raise ValueError("Outcome must follow the replay and fall within the reviewed observation window")
        normalized = _json({**row, "observed_at": observed.isoformat()})
        members.add(ReplaySourceInput("outcome", *identity, _digest(normalized)))
        observations[key] = (row["relevant"], observed)
    return observations, members


def _probability(value) -> Fraction:
    if type(value) not in (int, float) or not 0 <= value <= 1:
        raise ValueError("Forecast probabilities must be between zero and one")
    return Fraction(str(value))


def _forecasts(packet, population, evaluated_at, through, observations) -> dict:
    freeze = _source_time(packet["frozen_at"])
    if not evaluated_at <= freeze <= through or any(freeze >= observed for _, observed in observations.values()):
        raise ValueError("Forecasts must be frozen before observed outcomes")
    if not _matches(packet["method_sha256"], _DIGEST):
        raise ValueError("Expected an exact forecast method digest")
    if not isinstance(packet["predictions"], list):
        raise TypeError("Expected a prediction list")
    probabilities = {}
    for item in packet["predictions"]:
        row = _object(item, FORECAST_FIELDS, "forecast")
        key = row["candidate_id"]
        if (not _text(key) or key in probabilities or key not in population
                or row["candidate_input_sha256"] != population[key]):
            raise ValueError("Forecast does not identify a unique frozen candidate")
        probabilities[key] = {treatment: _probability(row[treatment]) for treatment in TREATMENTS}
    if probabilities.keys() != population.keys():
        raise ValueError("Forecasts must cover the entire replay population")
    return probabilities


def _ratio(value: Fraction) -> dict:
    return {"numerator": value.numerator, "denominator": value.denominator}


def _calibration(labels, probabilities, treatment) -> dict:
    groups = [[] for _ in range(5)]
    errors = []
    for key, label in labels.items():
        probability = probabilities[key][treatment]
        groups[min(4, int(probability * 5))].append((probability, int(label)))
        errors.append((probability - label) ** 2)
    bins = []
    for index, group in enumerate(groups):
        if group:
            count = len(group)
            bins.append({
                "index": index,
                "observations": count,
                "mean_probability": _ratio(sum(p for p, _ in group) / count),
                "observed_rate": _ratio(Fraction(sum(y for _, y in group), count)),
            })
    return {"observations": len(errors), "brier_score": _ratio(sum(errors) / len(errors)) if errors else None,
            "bins": bins}


def _ranking_metrics(ordered, labels) -> dict:
    positives = sum(labels.values())
    negatives = len(labels) - positives
    remaining_negatives, correct = negatives, 0
    for key in ordered:
        label = labels.get(key)
        if label is True:
            correct += remaining_negatives
        elif label is False:
            remaining_negatives -= 1
    top = labels.get(ordered[0])
    return {
        "correct_judged_pairs": correct,
        "judged_positive_negative_pairs": positives * negatives,
        "top_rank_outcome": top,
        "top_rank_regret": int(bool(positives)) - int(top) if len(labels) == len(ordered) else None,
    }


def compare_outcomes(frozen_input, replay_review, candidate, outcomes, outcome_review, forecasts=None) -> dict:
    source = frozen_input.read()
    if source.get("schema_version") != 2:
        raise ValueError("Outcome comparison requires governed replay inputs")
    rebuilt = replay_preferences(
        source, source_use=replay_review.path.read_bytes(), source_use_sha256=replay_review.sha256,
    )
    candidate_bytes = _json({**rebuilt, "frozen_input_file_sha256": frozen_input.sha256})
    if _digest(candidate_bytes) != candidate.sha256 or candidate.path.read_bytes() != candidate_bytes:
        raise ValueError("Ranking candidate did not reproduce its pinned bytes")

    binding = {"schema_version": 1, "frozen_input_sha256": frozen_input.sha256, "candidate_sha256": candidate.sha256}
    packet = _packet(binding, outcomes, {"observed_through", "source_use_sha256", "outcomes"}, "outcome packet")
    if packet["source_use_sha256"] != outcome_review.sha256:
        raise ValueError("Outcome packet does not bind the independently reviewed source-use manifest")
    evaluated_at = _source_time(rebuilt["baseline_ranking"]["evaluated_at"])
    through = _source_time(packet["observed_through"])
    if through <= evaluated_at:
        raise ValueError("Observation window must end after the replay")
    rankings = {treatment: rebuilt[f"{treatment}_ranking"]["candidates"] for treatment in TREATMENTS}
    population = {row["candidate_id"]: row["candidate_input_sha256"] for row in rankings["baseline"]}
    observations, members = _outcome_rows(packet["outcomes"], population, evaluated_at, through)
    verify_replay_source_use(outcome_review.read(), members, when=through)

    probabilities = None
    if forecasts is not None:
        predictions = _packet(binding, forecasts, {"frozen_at", "method_sha256", "predictions"}, "forecast packet")
        probabilities = _forecasts(predictions, population, evaluated_at, through, observations)
    labels = {key: label for key, (label, _) in observations.items()}
    metrics = {}
    for treatment, rows in rankings.items():
        metrics[treatment] = _ranking_metrics([row["candidate_id"] for row in rows], labels)
        metrics[treatment]["calibration"] = _calibration(labels, probabilities, treatment) if probabilities else None
    return {
        **binding, "kind": "observed-outcome-comparison", "evaluated_at": evaluated_at.isoformat(),
        "observed_through": through.isoformat(), "replay_source_use_sha256": replay_review.sha256,
        "outcomes_sha256": outcomes.sha256, "outcome_source_use_sha256": outcome_review.sha256,
        "forecasts_sha256": forecasts.sha256 if forecasts is not None else None,
        "candidate_count": len(population), "judged_candidates": len(labels), "metrics": metrics,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("input", "source-use", "candidate", "outcomes", "outcome-source-use", "forecasts"):
        parser.add_argument(f"--{name}", nargs=2, metavar=("FILE", "SHA256"), required=name != "forecasts")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    required = [args.input, args.source_use, args.candidate, args.outcomes, args.outcome_source_use]
    pinned = [PinnedFile(Path(path), pin) for path, pin in required]
    try:
        forecasts = PinnedFile(Path(args.forecasts[0]), args.forecasts[1]) if args.forecasts else None
        payload = _json(compare_outcomes(*pinned, forecasts=forecasts))
        _write_new(args.output, payload)
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        print("Outcome comparison rejected. Check reviewed replay, outcomes, and output pins.", file=sys.stderr)
        return 1
    print(f"Private outcome comparison SHA-256 {_digest(payload)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
