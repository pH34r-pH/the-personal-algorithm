"""Operator CLI for frozen snapshot seeding, verification, and versioned recovery.

Every command operates on explicit inputs and never overwrites an existing local
file or live Blob. Production writer fencing and cutover remain external controls.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .azure_blob_snapshot import configured_blob_client
from .locked_snapshot import locked_snapshot
from .snapshot_recovery import staged_versioned_snapshot
from .snapshot_seed import seed_frozen_snapshot, verify_frozen_seed

DEFAULT_MAX_BYTES = 512 * 1024 * 1024


def _copy_new_file(source: Path, destination: Path) -> tuple[int, str]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    digest = hashlib.sha256()
    size = 0
    with source.open("rb") as reader, os.fdopen(os.open(destination, flags, 0o600), "wb") as writer:
        while chunk := reader.read(1024 * 1024):
            digest.update(chunk)
            writer.write(chunk)
            size += len(chunk)
        writer.flush()
        os.fsync(writer.fileno())
    return size, digest.hexdigest()


def _run_seed(args: argparse.Namespace) -> dict[str, Any]:
    blob, close = configured_blob_client(args.blob_url, args.identity_client_id)
    try:
        fn = verify_frozen_seed if args.command == "verify-seed" else seed_frozen_snapshot
        receipt = fn(Path(args.source), blob, staging_dir=Path(args.staging_dir))
        return asdict(receipt)
    finally:
        close()


def _run_stage_version(args: argparse.Namespace) -> dict[str, Any]:
    destination = Path(args.output)
    if destination.exists():
        raise FileExistsError("refusing to overwrite existing output file")
    blob, close = configured_blob_client(
        args.blob_url, args.identity_client_id, version_id=args.version_id
    )
    try:
        with staged_versioned_snapshot(
            blob, version_id=args.version_id, staging_dir=Path(args.staging_dir),
            max_bytes=args.max_bytes,
        ) as (staged, receipt):
            size, digest = _copy_new_file(staged, destination)
            if size != receipt.size or digest != receipt.sha256:
                destination.unlink(missing_ok=True)
                raise RuntimeError("copied output did not match staged snapshot digest")
            result = asdict(receipt)
            result["output"] = str(destination)
            return result
    finally:
        close()


def _run_backup_locked(args: argparse.Namespace) -> dict[str, Any]:
    destination = Path(args.output)
    if destination.exists():
        raise FileExistsError("refusing to overwrite existing output file")
    with locked_snapshot(
        Path(args.source), staging_dir=Path(args.staging_dir), timeout=args.timeout
    ) as locked:
        size, digest = _copy_new_file(locked.path, destination)
        locked.verify()
        return {"size": size, "sha256": digest, "output": str(destination)}


def _add_blob_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--blob-url", required=True)
    parser.add_argument("--identity-client-id", required=True)
    parser.add_argument("--staging-dir", required=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m personal_algorithm.snapshot_cli")
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("seed", "verify-seed"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--source", required=True)
        _add_blob_args(cmd)

    stage = sub.add_parser("stage-version")
    _add_blob_args(stage)
    stage.add_argument("--version-id", required=True)
    stage.add_argument("--output", required=True)
    stage.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)

    backup = sub.add_parser("backup-locked")
    backup.add_argument("--source", required=True)
    backup.add_argument("--output", required=True)
    backup.add_argument("--staging-dir", required=True)
    backup.add_argument("--timeout", type=float, default=30.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    handlers = {
        "seed": _run_seed,
        "verify-seed": _run_seed,
        "stage-version": _run_stage_version,
        "backup-locked": _run_backup_locked,
    }
    try:
        receipt = handlers[args.command](args)
    except (OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
