"""Composition helper that protects private routers with instance auth."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from .instance_auth import InstanceAuth


def owner_dependencies(auth: InstanceAuth) -> list:
    """Dependencies to supply when including a private router.

    FastAPI snapshots router dependencies when routes are created, so mutating
    an existing router's dependency list does not retroactively protect them.
    """
    return [Depends(auth.dependency())]


def protect(router: APIRouter, auth: InstanceAuth) -> APIRouter:
    """Return a wrapper router that mounts the target behind owner auth."""
    wrapper = APIRouter(dependencies=owner_dependencies(auth))
    wrapper.include_router(router)
    return wrapper
