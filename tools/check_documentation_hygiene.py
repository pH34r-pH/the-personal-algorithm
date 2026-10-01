"""Check changed paths and select Markdown without parsing Markdown.

The policy is intentionally small: Git supplies rename metadata, and markdownlint/lychee
own Markdown syntax and link semantics in CI.
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
KNOWN_DERIVED_ROOTS = {"graphify-out"}
BROKEN_LINK_FIXTURE = Path("tests/fixtures/documentation-hygiene/broken-link.md")


def git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(root), *args])


def diff_entries(root: Path, base: str, head: str) -> list[tuple[str, str, bool]]:
    """Return (status, destination, is_new_or_rename) from Git's NUL stream."""
    raw = git(root, "diff", "--name-status", "-z", "--find-renames=50%", "--diff-filter=ACMR", base, head)
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
            index += 1  # old path
            destination = fields[index].decode("utf-8")
            index += 1
            entries.append((status, destination, True))
        else:
            destination = fields[index].decode("utf-8")
            index += 1
            entries.append((status, destination, status[0] in {"A", "C"}))
    return entries


def validate_paths(entries: Iterable[tuple[str, str, bool]]) -> list[str]:
    errors: list[str] = []
    for status, path, is_new in entries:
        if not is_new:
            continue
        name = Path(path).name
        stem = Path(name).stem
        if ISSUE_ONLY.fullmatch(stem):
            errors.append(f"{status}: new path has no descriptive name: {path}")
        parts = Path(path).parts
        if parts and parts[0] in KNOWN_DERIVED_ROOTS:
            continue
        if any(part in ARTIFACT_SEGMENTS for part in parts):
            errors.append(f"{status}: new path is inside a cache/temp/build artifact directory: {path}")
    return errors


def selected_docs(entries: Iterable[tuple[str, str, bool]]) -> list[str]:
    docs = {
        path
        for _, path, _ in entries
        if Path(path).suffix.lower() in {".md", ".markdown", ".mdx"}
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

        # A code-only change must produce zero bytes, not a blank line.
        (repo / "module.py").write_text("value = 1\n", encoding="utf-8")
        commit(repo, "code-only")
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--root", str(repo), "--base", first, "--head", "HEAD", "--print-docs"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        )
        if result.stdout != "":
            raise AssertionError(f"code-only selection was not empty: {result.stdout!r}")

        # Exercise actual Git rename metadata and permit a meaningful numbered series.
        (repo / "articles").mkdir()
        subprocess.run(["git", "-C", str(repo), "mv", "README.md", "articles/014-descriptive-topic.md"], check=True)
        commit(repo, "rename")
        entries, errors = run_check(repo, first, "HEAD")
        if errors or not any(status.startswith("R") and path == "articles/014-descriptive-topic.md" for status, path, _ in entries):
            raise AssertionError(f"rename regression: entries={entries!r}, errors={errors!r}")
        rename_docs = selected_docs(entries)
        if rename_docs != ["articles/014-descriptive-topic.md"]:
            raise AssertionError(f"unexpected rename selection: {rename_docs!r}")

        # Issue-number-only names and nested artifact directories are rejected.
        (repo / "issue-123.md").write_text("# Bad name\n", encoding="utf-8")
        (repo / "evidence" / "2026" / "cache").mkdir(parents=True)
        (repo / "evidence" / "2026" / "cache" / "receipt.md").write_text("# Generated\n", encoding="utf-8")
        commit(repo, "bad paths")
        _, errors = run_check(repo, first, "HEAD")
        if not any("issue-123.md" in error for error in errors):
            raise AssertionError(f"issue-number-only name was not rejected: {errors!r}")
        if not any("artifact" in error for error in errors):
            raise AssertionError(f"nested artifact path was not rejected: {errors!r}")


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
