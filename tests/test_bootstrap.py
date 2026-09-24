from datetime import UTC, datetime

from personal_algorithm.bootstrap import BootstrapCoordinator
from personal_algorithm.models import Candidate
from personal_algorithm.providers import (
    BootstrapCheckpoint,
    BootstrapStatus,
    Connection,
    ConnectionStatus,
    ProviderCapability,
    ProviderDescriptor,
)
from personal_algorithm.store import Store


class PagedProvider:
    descriptor = ProviderDescriptor(
        id="paged",
        display_name="Paged",
        capabilities=frozenset({ProviderCapability.HISTORY_IMPORT}),
    )

    def bootstrap(self, connection, checkpoint):
        page = int(checkpoint.cursor or "1")
        repos = [
            {
                "id": page,
                "full_name": f"fixture/repo-{page}",
                "html_url": f"https://example.invalid/repo-{page}",
            }
        ]
        return BootstrapCheckpoint(
            cursor="2" if page == 1 else None,
            imported=checkpoint.imported + 1,
            metadata={"repositories": repos, "last_page": page},
        )

    def repository_candidates(self, repositories):
        return tuple(
            Candidate(
                id=f"paged:{repo['id']}",
                source="paged",
                source_id=str(repo["id"]),
                canonical_url=repo["html_url"],
                title=repo["full_name"],
                discovered_at=datetime(2026, 9, 23, tzinfo=UTC),
                content_type="repository",
                provenance={"provider": "paged"},
            )
            for repo in repositories
        )


def _connection():
    return Connection(
        provider_id="paged",
        account_label="fixture",
        status=ConnectionStatus.CONNECTED,
        granted_scopes=(),
        connected_at=datetime(2026, 9, 23, tzinfo=UTC),
        credential_ref="memory://paged/fixture",
    )


def test_bootstrap_is_persisted_resumable_and_drops_transient_payloads():
    store = Store()
    coordinator = BootstrapCoordinator(store)
    provider = PagedProvider()
    connection = _connection()
    job = coordinator.create(connection)

    first, first_candidates = coordinator.step(
        job.id,
        connection=connection,
        provider=provider,
    )
    assert first.status is BootstrapStatus.RUNNING
    assert first.checkpoint.cursor == "2"
    assert first.checkpoint.imported == 1
    assert "repositories" not in first.checkpoint.metadata
    assert len(first_candidates) == 1

    # Re-create coordinator to prove progress lives in SQLite, not memory.
    resumed = BootstrapCoordinator(store)
    second, second_candidates = resumed.step(
        job.id,
        connection=connection,
        provider=provider,
    )
    assert second.status is BootstrapStatus.COMPLETE
    assert second.checkpoint.cursor is None
    assert second.checkpoint.imported == 2
    assert second.completed_at is not None
    assert len(second_candidates) == 1
    assert store.connection.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 2
