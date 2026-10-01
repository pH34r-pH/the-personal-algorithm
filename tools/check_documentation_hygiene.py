"""Check changed paths and select living Markdown without parsing Markdown.

Git supplies rename metadata; markdownlint and lychee own Markdown syntax and link
semantics in CI. Repository-specific living/history boundaries are explicit below.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from collections.abc import Iterable
from pathlib import Path

ISSUE_ONLY = re.compile(r"^(?:(?:issue|ticket|problem|pr)[-_ ]?)?\d+$", re.IGNORECASE)
CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
ARTIFACT_SEGMENTS = {
    ".cache",
    "cache",
    "tmp",
    "temp",
    "build",
    "dist",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".venv",
}
TEMP_SUFFIXES = {
    ".bak",
    ".crdownload",
    ".lock",
    ".orig",
    ".part",
    ".partial",
    ".pyc",
    ".pyo",
    ".rej",
    ".swp",
    ".swo",
    ".temp",
    ".tmp",
}
TEMP_FILENAMES = {".DS_Store"}
LIVING_DOC_FILES = {
    "README.md",
}
LIVING_DOC_PREFIXES = (
    "docs/",
    "deploy/",
)
HISTORY_OR_DERIVED_PREFIXES = (
    "docs/roadmap.md",
    "docs/m0.md",
    "docs/prior-art.md",
)
BROKEN_LINK_FIXTURE = Path("tests/fixtures/documentation-hygiene/broken-link.md")


def git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(root), *args])


def checked_path(raw: bytes) -> str:
    path = raw.decode("utf-8")
    if CONTROL_CHARS.search(path):
        raise RuntimeError(f"control character in changed path before CLI handoff: {path!r}")
    return path


def diff_entries(root: Path, base: str, head: str) -> list[tuple[str, str, bool]]:
    """Return (status, destination, is_new_or_rename) from Git's NUL stream."""
    raw = git(
        root,
        "diff",
        "--name-status",
        "-z",
        "--find-renames=50%",
        "--diff-filter=ACMR",
        base,
        head,
    )
    fields = raw.split(b"\0")
    entries: list[tuple[str, str, bool]] = []
    index = 0
    while index < len(fields) - 1:
        status = fields[index].decode("utf-8")
        index += 1
        if not status:
            continue
        if status[0] in {"R", "C"}:
            if index + 1 >= len(fields):
                raise RuntimeError(f"incomplete Git rename record: {status}")
            checked_path(fields[index])
            index += 1
            destination = checked_path(fields[index])
            index += 1
            entries.append((status, destination, True))
        else:
            destination = checked_path(fields[index])
            index += 1
            entries.append((status, destination, status[0] in {"A", "C"}))
    return entries


def under_prefix(path: str, prefix: str) -> bool:
    return path == prefix.rstrip("/") or path.startswith(prefix)


def is_living_doc(path: str) -> bool:
    if Path(path).name == "AGENTS.md":
        return True
    if path.startswith("living/") or path in LIVING_DOC_FILES:
        return True
    if any(under_prefix(path, prefix) for prefix in HISTORY_OR_DERIVED_PREFIXES):
        return False
    return any(under_prefix(path, prefix) for prefix in LIVING_DOC_PREFIXES)


def is_markdown(path: str) -> bool:
    return Path(path).suffix.lower() in {".md", ".markdown", ".mdx"}


def validate_paths(entries: Iterable[tuple[str, str, bool]]) -> list[str]:
    errors: list[str] = []
    for status, path, is_new in entries:
        if CONTROL_CHARS.search(path):
            raise RuntimeError(f"control character in changed path before CLI handoff: {path!r}")
        if not is_new:
            continue
        path_obj = Path(path)
        if path_obj.name in TEMP_FILENAMES or path_obj.suffix.lower() in TEMP_SUFFIXES:
            errors.append(f"{status}: new path has a temporary filename or suffix: {path}")
        if any(part in ARTIFACT_SEGMENTS for part in path_obj.parts):
            errors.append(f"{status}: new path is inside a cache/temp/build artifact directory: {path}")
        if is_markdown(path) and is_living_doc(path) and ISSUE_ONLY.fullmatch(path_obj.stem):
            errors.append(f"{status}: new living documentation path has no descriptive name: {path}")
    return errors


def selected_docs(entries: Iterable[tuple[str, str, bool]]) -> list[str]:
    docs = {
        path
        for _, path, _ in entries
        if is_markdown(path)
        and is_living_doc(path)
        and Path(path) != BROKEN_LINK_FIXTURE
        and not any(part in ARTIFACT_SEGMENTS for part in Path(path).parts)
    }
    return sorted(docs)


def check_fixture(root: Path) -> None:
    fixture = root / BROKEN_LINK_FIXTURE
    if not fixture.is_file() or "this-target-does-not-exist.md" not in fixture.read_text(encoding="utf-8"):
        raise RuntimeError(f"missing or changed negative link fixture: {BROKEN_LINK_FIXTURE}")


def run_check(root: Path, base: str, head: str) -> tuple[list[tuple[str, str, bool]], list[str]]:
    entries = diff_entries(root, base, head)
    errors = validate_paths(entries)
    return entries, errors


def commit(root: Path, message: str) -> None:
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", message], check=True)


def _assert_code_only(repo: Path, first: str) -> None:
    (repo / "module.py").write_text("value = 1\n", encoding="utf-8")
    commit(repo, "code-only")
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--root",
            str(repo),
            "--base",
            first,
            "--head",
            "HEAD",
            "--print-docs",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    if result.stdout != "":
        raise AssertionError(f"code-only selection was not empty: {result.stdout!r}")


def _assert_rename_and_numeric_evidence(repo: Path, first: str) -> None:
    (repo / "living").mkdir()
    subprocess.run(["git", "-C", str(repo), "mv", "README.md", "living/014-descriptive-topic.md"], check=True)
    commit(repo, "rename")
    entries, errors = run_check(repo, first, "HEAD")
    if errors or not any(status.startswith("R") and path == "living/014-descriptive-topic.md" for status, path, _ in entries):
        raise AssertionError(f"rename regression: entries={entries!r}, errors={errors!r}")
    if selected_docs(entries) != ["living/014-descriptive-topic.md"]:
        raise AssertionError(f"unexpected rename selection: {selected_docs(entries)!r}")

    (repo / "evidence" / "2026").mkdir(parents=True)
    (repo / "evidence" / "2026" / "123.json").write_text("{}\n", encoding="utf-8")
    commit(repo, "numeric evidence")
    entries, errors = run_check(repo, first, "HEAD")
    if errors or selected_docs(entries) != ["living/014-descriptive-topic.md"]:
        raise AssertionError(f"numeric evidence regression: entries={entries!r}, errors={errors!r}")


def _assert_derived_products(repo: Path, first: str) -> None:
    (repo / "graphify-out").mkdir()
    (repo / "graphify-out" / "GRAPH_REPORT.md").write_text("# Derived report\n", encoding="utf-8")
    commit(repo, "derived report")
    entries, errors = run_check(repo, first, "HEAD")
    if errors or "graphify-out/GRAPH_REPORT.md" in selected_docs(entries):
        raise AssertionError(f"derived report classification regression: entries={entries!r}, errors={errors!r}")

    (repo / "graphify-out" / "cache").mkdir()
    (repo / "graphify-out" / "cache" / "generated.md").write_text("# Cache\n", encoding="utf-8")
    commit(repo, "derived cache")
    _, errors = run_check(repo, first, "HEAD")
    if not any("artifact" in error for error in errors):
        raise AssertionError(f"derived cache was not rejected: {errors!r}")


def _assert_rejections(repo: Path, first: str) -> None:
    (repo / "living" / "issue-123.md").write_text("# Bad name\n", encoding="utf-8")
    (repo / "evidence" / "2026" / "cache").mkdir(parents=True)
    (repo / "evidence" / "2026" / "cache" / "receipt.md").write_text("# Generated\n", encoding="utf-8")
    (repo / "evidence" / "2026" / "receipt.tmp").write_text("temporary\n", encoding="utf-8")
    (repo / "evidence" / "2026" / "receipt.pyc").write_bytes(b"temporary")
    commit(repo, "bad paths")
    _, errors = run_check(repo, first, "HEAD")
    if not any("issue-123.md" in error for error in errors):
        raise AssertionError(f"issue-number-only name was not rejected: {errors!r}")
    if not any("artifact" in error for error in errors):
        raise AssertionError(f"nested artifact path was not rejected: {errors!r}")
    if not any("receipt.tmp" in error for error in errors) or not any("receipt.pyc" in error for error in errors):
        raise AssertionError(f"temporary suffixes were not rejected: {errors!r}")


def _assert_control_path(repo: Path, first: str) -> None:
    (repo / "living\ncontrol.md").write_text("# Control\n", encoding="utf-8")
    commit(repo, "control character")
    try:
        diff_entries(repo, first, "HEAD")
    except RuntimeError as error:
        if "control character" not in str(error):
            raise AssertionError(f"unexpected control-character error: {error}")
    else:
        raise AssertionError("control-character path was not rejected")


def self_test(root: Path) -> None:
    check_fixture(root)
    with tempfile.TemporaryDirectory(prefix="documentation-hygiene-") as temporary:
        repo = Path(temporary)
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Documentation Test"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "docs@example.invalid"], check=True)
        (repo / "README.md").write_text("# Fixture\n", encoding="utf-8")
        commit(repo, "initial")
        first = git(repo, "rev-parse", "HEAD").decode().strip()
        _assert_code_only(repo, first)
        _assert_rename_and_numeric_evidence(repo, first)
        _assert_rejections(repo, first)
        _assert_control_path(repo, first)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--print-docs", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    root = (args.root or Path(__file__).resolve().parents[1]).resolve()
    try:
        if args.self_test:
            self_test(root)
        entries, errors = run_check(root, args.base, args.head)
        if errors:
            for error in errors:
                print(error, file=sys.stderr)
            return 1
        if args.print_docs:
            sys.stdout.write("\n".join(selected_docs(entries)))
        return 0
    except (OSError, RuntimeError, subprocess.CalledProcessError, AssertionError) as error:
        print(f"documentation hygiene check failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
