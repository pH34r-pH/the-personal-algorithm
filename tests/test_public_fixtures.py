import json
import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]


def test_supported_entrypoints_use_the_expected_apps():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "uvicorn personal_algorithm.api:app --host 127.0.0.1 --reload" in readme

    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines()
    [command] = [line.removeprefix("CMD ") for line in dockerfile if line.startswith("CMD ")]
    assert json.loads(command) == [
        "uvicorn", "personal_algorithm.application:app_from_env", "--factory",
        "--host", "0.0.0.0", "--port", "8000", "--no-access-log",
    ]
    assert [line for line in dockerfile if line.startswith("COPY ")] == [
        "COPY pyproject.toml README.md LICENSE ./",
        "COPY src ./src",
    ]


def test_public_config_uses_local_paths_and_synthetic_example_values():
    config = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
    assert set(config) == {"database", "sources", "policy"}
    assert config["database"] == "data/personal-algorithm.sqlite3"
    for source in config["sources"]:
        assert set(source) <= {"type", "url", "query", "max_results"}
        if "url" in source:
            url = urlparse(source["url"])
            assert url.scheme == "https" and url.netloc == "example.com" and not url.query

    lines = (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    pairs = [line.split("=", 1) for line in lines if line and not line.startswith("#")]
    values = dict(pairs)
    assert len(values) == len(pairs)
    assert values["TPA_OWNER_OBJECT_ID"] == values["TPA_TRUSTED_HOST_AUTH"] == ""
    example_values = {
        "TPA_DATABASE": "data/personal-algorithm.sqlite3",
        "TPA_KEY_VAULT_URL": "https://YOUR-VAULT.vault.azure.net",
        "TPA_ARCHIVE_DIR": "data/archives",
        "TPA_ARCHIVE_MAX_BYTES": "53687091200",
    }
    assert all(value == example_values.get(name, "") for name, value in values.items())


def test_tracked_test_fixtures_exclude_private_archive_and_key_formats():
    tracked = subprocess.check_output(["git", "ls-files", "-z", "--", "tests/"], cwd=ROOT)
    private_format = re.compile(
        rb"(?:^|/)\.env(?:\.|$)|\.(?:sqlite\w*(?:-\w+)?|db(?:-\w+)?|zip|7z|tar|gz|tgz|rar|"
        rb"pem|key|pfx|p12|pkcs12|jks)$|(?:^|/)id_(?:rsa|dsa|ecdsa|ed25519)$",
        re.IGNORECASE,
    )
    assert not any(private_format.search(path) for path in tracked.split(b"\x00"))
