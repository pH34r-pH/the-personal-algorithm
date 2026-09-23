"""Minimal local HTTP boundary for M0."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException

from .ranking import ReferenceRanker
from .serialization import (
    ValidationError,
    candidate_from_dict,
    interaction_from_dict,
    policy_from_dict,
    ranked_to_dict,
)
from .store import Store


def create_app(store: Store | None = None) -> FastAPI:
    database = store or Store()
    ranker = ReferenceRanker()
    app = FastAPI(title="The Personal Algorithm", version="0.1.0")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/candidates", status_code=201)
    def ingest(payload: dict) -> dict[str, str]:
        try:
            candidate = candidate_from_dict(payload)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        database.put_candidate(candidate)
        return {"id": candidate.id}

    @app.post("/rank")
    def rank(payload: dict) -> dict:
        try:
            candidate = candidate_from_dict(payload["candidate"])
            policy = policy_from_dict(payload["policy"])
        except (KeyError, ValidationError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        ranked = ranker.rank(candidate, policy, now=datetime.now(UTC))
        database.put_candidate(candidate)
        database.put_ranking(ranked)
        return ranked_to_dict(ranked)

    @app.post("/interactions", status_code=201)
    def interact(payload: dict) -> dict[str, str]:
        try:
            interaction = interaction_from_dict(payload)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        try:
            database.put_interaction(interaction)
        except Exception as exc:
            # Foreign-key failures are a client boundary error; do not leak SQL details.
            raise HTTPException(status_code=409, detail="candidate does not exist") from exc
        return {"status": "recorded"}

    return app


app = create_app()
