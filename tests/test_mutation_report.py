from copy import deepcopy
from pathlib import Path
from runpy import run_path

import pytest

_validator = run_path(str(Path(__file__).parents[1] / "tools" / "validate_mutation_report.py"))
normalize_report = _validator["normalize_report"]
validate_report = _validator["validate_report"]


def _report():
    return {
        "schemaVersion": "2",
        "thresholds": {"high": 80, "low": 60},
        "framework": {"name": "irradiate", "version": "0.4.3"},
        "files": {
            "personal_algorithm/ranking.py": {
                "language": "python",
                "source": "",
                "mutants": [
                    {
                        "id": "ranking-1",
                        "status": "Killed",
                        "description": "replaced `+` with `-`",
                        "mutatorName": "binop_swap",
                        "coveredBy": ["tests/test_ranking.py::test_example"],
                        "location": {
                            "start": {"line": 1, "column": 1},
                            "end": {"line": 1, "column": 2},
                        },
                    }
                ],
            }
        },
    }


def test_validate_report_checks_schema_and_required_files():
    summary = validate_report(_report(), required_files=("personal_algorithm/ranking.py",))

    assert summary.files == 1
    assert summary.mutants == 1
    assert summary.statuses == {"Killed": 1}


def test_validate_report_rejects_missing_required_file():
    with pytest.raises(ValueError, match="missing required files"):
        validate_report(_report(), required_files=("personal_algorithm/ingest.py",))


def test_validate_report_rejects_nonstandard_status():
    report = _report()
    report["files"]["personal_algorithm/ranking.py"]["mutants"][0]["status"] = "NoTests"

    with pytest.raises(ValueError, match="official Stryker schema v2 violation"):
        validate_report(report)


def test_normalize_report_records_known_irradiate_duplicate_provenance():
    report = _report()
    mutant = report["files"]["personal_algorithm/ranking.py"]["mutants"][0]
    mutant["id"] = "duplicate-1"
    mutant["duration"] = 12
    duplicate = deepcopy(mutant)
    duplicate["status"] = "NoCoverage"
    duplicate["duration"] = 0
    report["files"]["personal_algorithm/ranking.py"]["mutants"].append(duplicate)

    normalized, summary = normalize_report(
        report,
        required_files=("personal_algorithm/ranking.py",),
    )

    assert summary.mutants == 1
    assert summary.statuses == {"Killed": 1}
    assert summary.reconciled_duplicate_ids == ("duplicate-1",)
    provenance = normalized["config"]["personalAlgorithmReportNormalization"]
    assert provenance["reconciled_duplicates"][0]["discarded_status"] == "NoCoverage"
    validate_report(normalized, required_files=("personal_algorithm/ranking.py",))


def test_normalize_report_rejects_ambiguous_duplicate_statuses():
    report = _report()
    mutant = report["files"]["personal_algorithm/ranking.py"]["mutants"][0]
    duplicate = deepcopy(mutant)
    duplicate["status"] = "Survived"
    report["files"]["personal_algorithm/ranking.py"]["mutants"].append(duplicate)

    with pytest.raises(ValueError, match="ambiguous duplicate mutant id"):
        normalize_report(report)
