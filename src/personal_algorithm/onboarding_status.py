"""Configuration evidence, deliberately not an authenticated live-readiness claim."""

import os

from .archive_importers import ARCHIVE_IMPORTERS, IMPORT_COVERAGE


def onboarding_status(registry, connections, archives) -> dict:
    configured = {item.id for item in registry.descriptors()}
    connected = {item.provider_id: item for item in connections.list()}
    providers = []
    for provider in ("github", "google", "spotify", "reddit", "messages", "x", "linkedin", "meta", "browser"):
        oauth = "not_implemented"
        if provider == "github":
            oauth = "configured" if provider in configured else "operator_setup_required"
        connection = connected.get(provider)
        providers.append({
            "id": provider,
            "oauth": oauth,
            "saved_connection": connection is not None,
            "connection_status": connection.status.value if connection else None,
            "live_access": "not_verified",
            "archive_parser": "implemented" if provider in ARCHIVE_IMPORTERS else "not_implemented",
            "archive_coverage": IMPORT_COVERAGE.get(provider, "Retained only; parser not implemented"),
            "bootstrap": "browser_driven_repository_listing" if provider == "github" else "not_implemented",
            "continuous_sync": "not_implemented",
        })
    return {
        "scope": "configuration_only",
        "source_revision": os.getenv("TPA_SOURCE_REVISION") or None,
        "deployment_revision": os.getenv("CONTAINER_APP_REVISION") or None,
        "live_checks": "not_verified",
        "durable_worker": "not_implemented",
        "archive_storage": "configured" if archives is not None else "operator_setup_required",
        "archive_count": len(archives.list()) if archives is not None else 0,
        "providers": providers,
    }
