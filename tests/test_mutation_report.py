import pytest

from tools.validate_mutation_report import validate_report


def _report():
    return {
        "schemaVersion": "2",
        "framework": {"name": "irradiate", "version": "0.4.3"},
        "files": {
            "personal_algorithm/ranking.py": {
                "language": "python",
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
