"""Validate and reconcile native Stryker mutation reports.

The vendored schema is the official Stryker v2 schema at commit
``a54fe7efa9904c8f3574b969482c23581d031c91``. Irradiate 0.4.3 currently
exports a small, known duplicate pair for staticmethod-removal mutants; that
pair is reconciled explicitly and recorded in the report configuration.
"""

from __future__ import annotations

import argparse
import copy
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA_PATH = Path(__file__).with_name("mutation-testing-report-schema-v2.json")
SCHEMA_COMMIT = "a54fe7efa9904c8f3574b969482c23581d031c91"
SCHEMA_SOURCE = (
    "https://raw.githubusercontent.com/stryker-mutator/mutation-testing-elements/"
    f"{SCHEMA_COMMIT}/packages/report-schema/src/mutation-testing-report-schema.json"
)
KNOWN_DUPLICATE_RULE = "irradiate-0.4.3-zero-duration-nocoverage-export-duplicate"
TESTED_STATUSES = {"Killed"}


class ReportError(ValueError):
    """Raised when a report is invalid or cannot be reconciled safely."""


@dataclass(frozen=True, slots=True)
class ReportSummary:
    files: int
    mutants: int
    statuses: dict[str, int]
    reconciled_duplicate_ids: tuple[str, ...] = ()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ReportError(message)


def _official_schema() -> dict[str, Any]:
    try:
        return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReportError(f"cannot load vendored Stryker schema: {exc}") from exc


def _validate_official_schema(report: Any) -> None:
    try:
        from jsonschema import Draft7Validator
    except ImportError as exc:
        raise ReportError("jsonschema is required to validate the vendored Stryker schema") from exc

    errors = sorted(
        Draft7Validator(_official_schema()).iter_errors(report),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        error = errors[0]
        path = "".join(f"[{part!r}]" for part in error.absolute_path) or "<root>"
        raise ReportError(f"official Stryker schema v2 violation at {path}: {error.message}")


def _mutant_identity(filename: str, mutant: dict[str, Any]) -> tuple[Any, ...]:
    """Return fields that identify the mutation independently of its result."""

    return (
        filename,
        mutant.get("mutatorName"),
        mutant.get("description"),
        json.dumps(mutant.get("location"), sort_keys=True),
        mutant.get("replacement"),
        tuple(mutant.get("coveredBy", [])),
    )


def _duplicate_groups(report: dict[str, Any]) -> dict[str, list[tuple[str, dict[str, Any]]]]:
    groups: defaultdict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for filename, file_report in report["files"].items():
        for mutant in file_report["mutants"]:
            groups[mutant["id"]].append((filename, mutant))
    return {mutant_id: group for mutant_id, group in groups.items() if len(group) > 1}


def _resolve_known_duplicate(
    mutant_id: str,
    group: list[tuple[str, dict[str, Any]]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    files = {filename for filename, _ in group}
    _require(len(files) == 1, f"ambiguous duplicate mutant id {mutant_id!r} spans files")
    identities = {_mutant_identity(filename, mutant) for filename, mutant in group}
    _require(
        len(identities) == 1,
        f"ambiguous duplicate mutant id {mutant_id!r} has different mutation identities",
    )
    _require(len(group) == 2, f"ambiguous duplicate mutant id {mutant_id!r} has {len(group)} records")

    no_coverage = [mutant for _, mutant in group if mutant["status"] == "NoCoverage"]
    tested = [mutant for _, mutant in group if mutant["status"] in TESTED_STATUSES]
    _require(
        len(no_coverage) == 1 and len(tested) == 1 and no_coverage[0].get("duration") == 0,
        f"ambiguous duplicate mutant id {mutant_id!r} has conflicting statuses",
    )

    retained = tested[0]
    provenance = {
        "id": mutant_id,
        "file": next(iter(files)),
        "rule": KNOWN_DUPLICATE_RULE,
        "retained_status": retained["status"],
        "discarded_status": "NoCoverage",
        "discarded_duration": no_coverage[0]["duration"],
        "source_framework": "irradiate",
        "source_version": "0.4.3",
    }
    return retained, provenance


def _validate_project_contract(
    report: dict[str, Any],
    *,
    required_files: tuple[str, ...],
    require_unique_ids: bool,
) -> ReportSummary:
    _require(report.get("schemaVersion", "").split(".", 1)[0] == "2", "report must use Stryker schema v2")
    framework = report.get("framework")
    _require(isinstance(framework, dict), "report framework must be an object")
    _require(isinstance(framework.get("name"), str), "report framework.name must be a string")
    _require(isinstance(framework.get("version"), str), "report framework.version must be a string")

    files = report["files"]
    missing_files = sorted(set(required_files) - files.keys())
    _require(not missing_files, f"report is missing required files: {', '.join(missing_files)}")

    statuses: Counter[str] = Counter()
    ids: list[str] = []
    for filename, file_report in files.items():
        _require(file_report.get("language") == "python", f"{filename}: language must be python")
        _require(isinstance(file_report.get("source"), str), f"{filename}: source must be a string")
        for mutant in file_report["mutants"]:
            mutant_id = mutant["id"]
            ids.append(mutant_id)
            statuses[mutant["status"]] += 1

    if require_unique_ids:
        duplicates = sorted(mutant_id for mutant_id, count in Counter(ids).items() if count > 1)
        _require(not duplicates, f"duplicate mutant IDs require explicit reconciliation: {', '.join(duplicates)}")

    return ReportSummary(files=len(files), mutants=len(ids), statuses=dict(statuses))


def validate_report(report: Any, *, required_files: tuple[str, ...] = ()) -> ReportSummary:
    """Validate the official Stryker v2 schema and project-specific invariants."""

    _require(isinstance(report, dict), "report root must be an object")
    _validate_official_schema(report)
    return _validate_project_contract(
        report,
        required_files=required_files,
        require_unique_ids=True,
    )


def normalize_report(
    report: Any,
    *,
    required_files: tuple[str, ...] = (),
) -> tuple[dict[str, Any], ReportSummary]:
    """Reconcile only the known irradiate duplicate and reject other conflicts."""

    _require(isinstance(report, dict), "report root must be an object")
    _validate_official_schema(report)
    _validate_project_contract(
        report,
        required_files=required_files,
        require_unique_ids=False,
    )

    normalized = copy.deepcopy(report)
    duplicate_groups = _duplicate_groups(normalized)
    reconciled: list[dict[str, Any]] = []
    retained_by_id: dict[str, dict[str, Any]] = {}
    for mutant_id, group in duplicate_groups.items():
        retained, provenance = _resolve_known_duplicate(mutant_id, group)
        retained_by_id[mutant_id] = retained
        reconciled.append(provenance)

    for file_report in normalized["files"].values():
        kept: list[dict[str, Any]] = []
        for mutant in file_report["mutants"]:
            mutant_id = mutant["id"]
            if mutant_id not in retained_by_id:
                kept.append(mutant)
                continue
            if mutant is retained_by_id[mutant_id]:
                kept.append(mutant)
        file_report["mutants"] = kept

    if reconciled:
        config = normalized.get("config", {})
        _require(isinstance(config, dict), "report config must be an object")
        _require(
            "personalAlgorithmReportNormalization" not in config,
            "report already contains normalization provenance",
        )
        config["personalAlgorithmReportNormalization"] = {
            "schema_source": SCHEMA_SOURCE,
            "duplicate_policy": KNOWN_DUPLICATE_RULE,
            "reconciled_duplicates": reconciled,
        }
        normalized["config"] = config

    summary = validate_report(normalized, required_files=required_files)
    return normalized, ReportSummary(
        files=summary.files,
        mutants=summary.mutants,
        statuses=summary.statuses,
        reconciled_duplicate_ids=tuple(item["id"] for item in reconciled),
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--normalize-to", type=Path)
    parser.add_argument("--require-file", action="append", default=[], dest="required_files")
    return parser.parse_args()


def _summary_text(summary: ReportSummary, *, normalized: bool) -> str:
    counts = ", ".join(f"{status}={count}" for status, count in sorted(summary.statuses.items()))
    action = "normalized and validated" if normalized else "validated"
    provenance = f", reconciled={len(summary.reconciled_duplicate_ids)}" if normalized else ""
    return f"{action} Stryker mutation report v2: files={summary.files}, mutants={summary.mutants}{provenance}; {counts}"


def main() -> int:
    args = _parse_args()
    try:
        report = json.loads(args.report.read_text(encoding="utf-8"))
        if args.normalize_to:
            normalized, summary = normalize_report(
                report,
                required_files=tuple(args.required_files),
            )
            args.normalize_to.write_text(
                json.dumps(normalized, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            print(_summary_text(summary, normalized=True))
        else:
            summary = validate_report(report, required_files=tuple(args.required_files))
            print(_summary_text(summary, normalized=False))
    except (OSError, json.JSONDecodeError, ReportError) as exc:
        print(f"invalid mutation report: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
