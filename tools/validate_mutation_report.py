"""Validate the project-required subset of the Stryker report schema v2."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

VALID_STATUSES = {
    "Killed",
    "Survived",
    "NoCoverage",
    "RuntimeError",
    "CompileError",
    "Ignored",
    "NoTests",
    "Timeout",
}


@dataclass(frozen=True, slots=True)
class ReportSummary:
    files: int
    mutants: int
    statuses: dict[str, int]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _validate_location(location: Any, *, mutant_id: str) -> None:
    _require(isinstance(location, dict), f"{mutant_id}: location must be an object")
    for endpoint in ("start", "end"):
        point = location.get(endpoint)
        _require(isinstance(point, dict), f"{mutant_id}: location.{endpoint} must be an object")
        for coordinate in ("line", "column"):
            value = point.get(coordinate)
            _require(
                isinstance(value, int) and value >= 1,
                f"{mutant_id}: location.{endpoint}.{coordinate} must be a positive integer",
            )


def validate_report(report: Any, *, required_files: tuple[str, ...] = ()) -> ReportSummary:
    """Validate required Stryker v2 fields and return a compact status summary."""

    _require(isinstance(report, dict), "report root must be an object")
    _require(report.get("schemaVersion") == "2", "report schemaVersion must be '2'")
    framework = report.get("framework")
    _require(isinstance(framework, dict), "report framework must be an object")
    _require(isinstance(framework.get("name"), str), "report framework.name must be a string")
    _require(isinstance(framework.get("version"), str), "report framework.version must be a string")

    files = report.get("files")
    _require(isinstance(files, dict), "report files must be an object")
    missing_files = sorted(set(required_files) - files.keys())
    _require(not missing_files, f"report is missing required files: {', '.join(missing_files)}")

    statuses: Counter[str] = Counter()
    for filename, file_report in files.items():
        _require(isinstance(filename, str) and filename, "report file names must be strings")
        _require(isinstance(file_report, dict), f"{filename}: file report must be an object")
        _require(file_report.get("language") == "python", f"{filename}: language must be python")
        mutants = file_report.get("mutants")
        _require(isinstance(mutants, list), f"{filename}: mutants must be an array")
        for mutant in mutants:
            _require(isinstance(mutant, dict), f"{filename}: mutant must be an object")
            mutant_id = mutant.get("id")
            _require(isinstance(mutant_id, str) and mutant_id, f"{filename}: mutant id is required")
            status = mutant.get("status")
            _require(status in VALID_STATUSES, f"{mutant_id}: unsupported status {status!r}")
            _require(isinstance(mutant.get("description"), str), f"{mutant_id}: description is required")
            _require(isinstance(mutant.get("mutatorName"), str), f"{mutant_id}: mutatorName is required")
            _require(isinstance(mutant.get("coveredBy"), list), f"{mutant_id}: coveredBy must be an array")
            _validate_location(mutant.get("location"), mutant_id=mutant_id)
            statuses[status] += 1

    return ReportSummary(files=len(files), mutants=sum(statuses.values()), statuses=dict(statuses))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--require-file", action="append", default=[], dest="required_files")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        report = json.loads(args.report.read_text(encoding="utf-8"))
        summary = validate_report(report, required_files=tuple(args.required_files))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"invalid mutation report: {exc}")
        return 1

    counts = ", ".join(f"{status}={count}" for status, count in sorted(summary.statuses.items()))
    print(f"valid Stryker mutation report v2: files={summary.files}, mutants={summary.mutants}; {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
