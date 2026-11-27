# Blob snapshot recovery: draft qualification

The Blob snapshot path is not deployed. The current Azure Files snapshot and
archives remain the production sources of truth. This document and the staging
helper do not authorize or perform a production cutover or restore.

## What the recovery helper does

`staged_versioned_snapshot` accepts a trusted Blob client already bound to one
explicit version ID, a trusted private staging directory, and a size limit. It
requires matching version metadata and an ETag-conditioned download, checks the
reported and streamed sizes, and validates SQLite integrity and required tables.
It yields a temporary copy and a digest; it does not overwrite the current Blob,
the local database, an archive, or any production file. On normal exit it removes
the temporary directory. A process or machine crash can leave private data in
staging, so an operator workflow must address staging protection and cleanup.

Version and ETag identify bytes, not application correctness. A complete recovery
must select the intended point in time, consider archive consistency and any
committed work after that point, independently fence all writers, preserve a
separate backup, and verify authentication and representative application data.
Do not automatically choose the newest version or replace live state based solely
on a successful SQLite check.

An isolated Azure Storage rehearsal enabled versioning, wrote two synthetic
databases, staged the earlier version, verified the current blob remained
unchanged, and rejected an unversioned client. Immediately after the management
API reported versioning enabled, uploads initially did not return a version ID;
later writes did. A deployment must establish actual version creation and
recovery with its target settings, not treat the configuration readback alone
as proof. The disposable storage account was deleted afterward.

## Access and durability limits

A separate synthetic managed-identity trial used a custom role granting Blob
data read and write at one container. It could read and publish under a lease,
while Blob and container deletion were denied. It could still create a different
blob in the same container and break its own lease. The runtime therefore needs
a dedicated container and a trusted-writer model; a lease is not protection
against a compromised or incorrectly privileged writer. Azure role assignments
are additive, so deployment must also inspect inherited grants and access through
other credentials. The [Blob lease documentation](https://learn.microsoft.com/en-us/rest/api/storageservices/lease-blob)
also states that a blob lease does not prevent deletion of its container.

Versioning and a narrower runtime role do not protect against every account or
container failure. Before migration, define and test independently protected
backups, retention and recovery, account/container deletion controls, the exact
runtime and operator roles, and their cost. The current experiments do not
establish those controls or a safe automatic rollback.

## Operator CLI and request-body bounds

`python -m personal_algorithm.snapshot_cli` exposes four explicit, non-destructive
subcommands that emit JSON receipts without printing database contents:

- `backup-locked` — captures a writer-reserved validated SQLite copy into a new `--output` file.
- `seed` — uploads a previously frozen SQLite file to an absent Blob (`overwrite=False`) and verifies readback.
- `verify-seed` — compares a frozen local file against an existing Blob without mutating either side.
- `stage-version` — downloads and validates one explicit `--version-id` into a new `--output` file.

When the opt-in fenced Blob mode is enabled (`TPA_BLOB_SNAPSHOT_URL` and
`TPA_BLOB_IDENTITY_CLIENT_ID`), `TPA_BODY_IDLE_TIMEOUT` (default `120` seconds)
and optional `TPA_BODY_MAX_DURATION` bound request-body stalls and continuously
slow uploads before response headers.
