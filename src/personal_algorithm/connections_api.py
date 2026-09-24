"""Private API surface for provider connections and manual bootstrap."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

from .bootstrap import BootstrapCoordinator
from .connections import ConnectionStore
from .github_auth import GitHubAuthorization
from .github_provider import GitHubProvider
from .provider_registry import ProviderRegistry
from .providers import ProviderCapability


def create_connections_router(
    *,
    registry: ProviderRegistry,
    connections: ConnectionStore,
    bootstrap: BootstrapCoordinator,
    github_auth: GitHubAuthorization | None = None,
    github_provider: GitHubProvider | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/connections", tags=["connections"])

    @router.get("")
    def list_connections() -> dict:
        connected = {item.provider_id: item for item in connections.list()}
        providers = []
        for descriptor in registry.descriptors():
            connection = connected.get(descriptor.id)
            providers.append(
                {
                    "id": descriptor.id,
                    "display_name": descriptor.display_name,
                    "capabilities": sorted(cap.value for cap in descriptor.capabilities),
                    "requested_scopes": list(descriptor.requested_scopes),
                    "connected": connection is not None,
                    "account_label": connection.account_label if connection else None,
                    "status": connection.status.value if connection else "disconnected",
                }
            )
        return {"providers": providers}

    @router.get("/github/connect")
    def github_connect(redirect_uri: str):
        if github_auth is None:
            raise HTTPException(status_code=503, detail="GitHub authorization is not configured")
        return RedirectResponse(github_auth.begin(redirect_uri))

    @router.get("/github/callback")
    def github_callback(code: str, state: str):
        if github_auth is None:
            raise HTTPException(status_code=503, detail="GitHub authorization is not configured")
        try:
            connection = github_auth.complete(code=code, state=state)
        except Exception as exc:
            raise HTTPException(status_code=400, detail="GitHub authorization failed") from exc
        connections.put(connection)
        return {
            "provider": "github",
            "connected": True,
            "account_label": connection.account_label,
        }

    @router.post("/{provider_id}/bootstrap", status_code=201)
    def start_bootstrap(provider_id: str):
        try:
            connection = connections.get(provider_id)
            provider = registry.get(provider_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if ProviderCapability.HISTORY_IMPORT not in provider.descriptor.capabilities:
            raise HTTPException(status_code=409, detail="provider does not support history import")
        job = bootstrap.create(connection)
        return _job(job)

    @router.post("/{provider_id}/bootstrap/{job_id}/step")
    def step_bootstrap(provider_id: str, job_id: str):
        try:
            connection = connections.get(provider_id)
            provider = registry.get(provider_id)
            job, candidates = bootstrap.step(
                job_id,
                connection=connection,
                provider=provider,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {**_job(job), "candidates_imported_this_step": len(candidates)}

    @router.get("/{provider_id}/bootstrap/{job_id}")
    def get_bootstrap(provider_id: str, job_id: str):
        try:
            job = bootstrap.get(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if job.provider_id != provider_id:
            raise HTTPException(status_code=404, detail="bootstrap job not found for provider")
        return _job(job)

    return router


def _job(job) -> dict:
    checkpoint = asdict(job.checkpoint)
    return {
        "id": job.id,
        "provider_id": job.provider_id,
        "status": job.status.value,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "checkpoint": checkpoint,
        "error": job.error,
    }
