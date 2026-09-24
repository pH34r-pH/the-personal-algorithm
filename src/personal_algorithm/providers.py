"""Provider capabilities, connections, and finite bootstrap job contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol


class ProviderCapability(StrEnum):
    SIGN_IN = "sign_in"
    CANDIDATE_DISCOVERY = "candidate_discovery"
    HISTORY_IMPORT = "history_import"
    ARCHIVE_REQUEST = "archive_request"
    ARCHIVE_IMPORT = "archive_import"
    CONTINUOUS_SYNC = "continuous_sync"


class ConnectionStatus(StrEnum):
    DISCONNECTED = "disconnected"
    CONNECTED = "connected"
    EXPIRED = "expired"
    ERROR = "error"


class BootstrapStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class ProviderDescriptor:
    id: str
    display_name: str
    capabilities: frozenset[ProviderCapability]
    requested_scopes: tuple[str, ...] = ()
    archive_instructions_url: str | None = None


@dataclass(frozen=True, slots=True)
class Connection:
    provider_id: str
    account_label: str
    status: ConnectionStatus
    granted_scopes: tuple[str, ...]
    connected_at: datetime
    credential_ref: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class BootstrapCheckpoint:
    cursor: str | None = None
    imported: int = 0
    skipped: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class BootstrapJob:
    id: str
    provider_id: str
    connection_ref: str
    status: BootstrapStatus
    started_at: datetime | None = None
    completed_at: datetime | None = None
    checkpoint: BootstrapCheckpoint = field(default_factory=BootstrapCheckpoint)
    error: str | None = None


class Provider(Protocol):
    descriptor: ProviderDescriptor

    def bootstrap(
        self,
        connection: Connection,
        checkpoint: BootstrapCheckpoint,
    ) -> BootstrapCheckpoint:
        """Run one resumable historical-import step."""
        ...
