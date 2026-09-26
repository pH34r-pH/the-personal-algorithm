"""Composition helper that protects private routers with instance auth."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from .instance_auth import InstanceAuth


def owner_dependencies(auth: InstanceAuth, *, redirect_unauthenticated: bool = False) -> list:
    """Dependencies to supply when including a private router.

    FastAPI snapshots router dependencies when routes are created, so mutating
    an existing router's dependency list does not retroactively protect them.
    """
    return [Depends(auth.dependency(redirect_unauthenticated=redirect_unauthenticated))]


def protect(
    router: APIRouter,
    auth: InstanceAuth,
    *,
    redirect_unauthenticated: bool = False,
) -> APIRouter:
    """Return a wrapper router that mounts the target behind owner auth."""
    wrapper = APIRouter(
        dependencies=owner_dependencies(
            auth,
            redirect_unauthenticated=redirect_unauthenticated,
        )
    )
    wrapper.include_router(router)
    return wrapper
