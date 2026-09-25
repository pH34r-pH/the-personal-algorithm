"""Single-owner authorization behind a trusted hosting authentication layer."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Header, HTTPException


@dataclass(frozen=True, slots=True)
class InstanceAuth:
    owner_subject: str
    identity_header: str = "x-ms-client-principal-id"

    def __post_init__(self) -> None:
        if not self.owner_subject.strip():
            raise ValueError("owner_subject must not be empty")

    def dependency(self):
        expected = self.owner_subject

        def require_owner(
            subject: str | None = Header(default=None, alias=self.identity_header),
        ) -> str:
            if subject is None:
                raise HTTPException(status_code=401, detail="authentication required")
            if subject != expected:
                raise HTTPException(status_code=403, detail="instance owner required")
            return subject

        return require_owner
