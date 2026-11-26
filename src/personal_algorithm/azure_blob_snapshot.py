"""Construct an explicitly configured, managed-identity Blob snapshot client."""

from __future__ import annotations

import re
from urllib.parse import urlsplit
from uuid import UUID

from azure.identity import ManagedIdentityCredential
from azure.storage.blob import BlobClient

from .leased_snapshot import LeasedSnapshot

_HOST = re.compile(r"[a-z0-9]{3,24}\.blob\.core\.windows\.net\Z")
_CONTAINER = re.compile(r"[a-z0-9](?:[a-z0-9-]{1,61})[a-z0-9]\Z")
_BLOB = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,254}\Z")


def _parse_target(url: str, identity_client_id: str) -> tuple[str, str, str, str]:
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        segments = parts.path.split("/")
        identity = str(UUID(identity_client_id))
        valid = (
            parts.scheme == "https" and parts.netloc == host and _HOST.fullmatch(host)
            and not parts.query and not parts.fragment and len(segments) == 3
            and _CONTAINER.fullmatch(segments[1]) and "--" not in segments[1]
            and _BLOB.fullmatch(segments[2])
        )
    except (TypeError, ValueError, AttributeError):
        valid = False
    if not valid:
        raise ValueError("invalid managed-identity Blob snapshot configuration")
    return f"https://{host}", segments[1], segments[2], identity


def configured_blob_snapshot(url: str, identity_client_id: str) -> LeasedSnapshot:
    """Use one existing Blob and an explicit user-assigned identity, without SAS."""
    account_url, container, name, client_id = _parse_target(url, identity_client_id)
    # Fail closed on the first network error. These are per-socket timeouts,
    # not a guarantee of total startup or multi-block upload duration.
    options = {"connection_timeout": 5, "read_timeout": 10, "retry_total": 0,
               "retry_connect": 0, "retry_read": 0, "retry_status": 0}
    credential = ManagedIdentityCredential(client_id=client_id, **options)
    try:
        blob = BlobClient(account_url=account_url, container_name=container, blob_name=name,
                          credential=credential, **options)
    except Exception:
        credential.close()
        raise

    def close_clients() -> None:
        try:
            blob.close()
        finally:
            credential.close()

    return LeasedSnapshot(blob, on_close=close_clients)
