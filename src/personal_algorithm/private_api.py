"""Composition helper that protects private routers with instance auth."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from .instance_auth import InstanceAuth


def protect(router: APIRouter, auth: InstanceAuth) -> APIRouter:
    """Require the configured owner identity for every route on a router."""
    router.dependencies.append(Depends(auth.dependency()))
    return router
