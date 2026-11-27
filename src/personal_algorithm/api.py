"""Minimal local HTTP boundary for M0."""

from __future__ import annotations

from sqlite3 import IntegrityError

from fastapi import FastAPI, HTTPException

from .ranking_runs import create_ranking_run, ranking_run_to_dict
from .serialization import (
    ValidationError,
    candidate_from_dict,
    impression_to_dict,
    interaction_from_dict,
    policy_from_dict,
    presentation_from_dict,
)
from .store import Store


def create_app(store: Store | None = None) -> FastAPI:
    database = store or Store()
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

    @app.post("/interactions", status_code=201)
    def interact(payload: dict) -> dict[str, str]:
        try:
            interaction = interaction_from_dict(payload)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        try:
            database.put_interaction(interaction)
        except (IntegrityError, ValueError) as exc:
            raise HTTPException(status_code=409, detail="candidate or prior presentation could not be verified") from exc
        return {"status": "recorded"}

    _register_ranking_routes(app, database)
    _register_presentation_routes(app, database)
    return app


def _register_ranking_routes(app: FastAPI, database: Store) -> None:
    def persist_run(candidates: list, raw_policy) -> dict:
        try:
            policy = policy_from_dict(raw_policy)
            run = create_ranking_run(map(candidate_from_dict, candidates), policy)
            database.put_ranking_run(run)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except IntegrityError as exc:
            raise HTTPException(status_code=409, detail="ranking run conflicts with stored data") from exc
        return ranking_run_to_dict(run)

    @app.post("/rank")
    def rank(payload: dict) -> dict:
        run = persist_run([payload.get("candidate")], payload.get("policy"))
        return {**run["candidates"][0], "run_id": run["id"]}

    @app.post("/ranking-runs", status_code=201)
    def rank_candidates(payload: dict) -> dict:
        candidates = payload.get("candidates")
        if not isinstance(candidates, list):
            raise HTTPException(status_code=422, detail="candidates must be a list of candidate objects")
        return persist_run(candidates, payload.get("policy"))

    @app.get("/ranking-runs/{identifier}")
    def read_ranking_run(identifier: str) -> dict:
        try:
            return ranking_run_to_dict(database.get_ranking_run(identifier))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="ranking run does not exist") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail="retained ranking evidence is malformed") from exc


def _register_presentation_routes(app: FastAPI, database: Store) -> None:
    @app.post("/impressions", status_code=201)
    def present(payload: dict) -> dict:
        try:
            run_id, candidate_ids, presented_at, identifier = presentation_from_dict(payload)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        try:
            impression = database.record_impression(
                run_id, candidate_ids, presented_at=presented_at, identifier=identifier,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="ranking run does not exist") from exc
        except (ValueError, IntegrityError) as exc:
            raise HTTPException(status_code=409, detail="presentation conflicts with retained evidence") from exc
        return impression_to_dict(impression)

    @app.get("/impressions/{identifier}")
    def read_impression(identifier: str) -> dict:
        try:
            return impression_to_dict(database.get_impression(identifier))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="presentation evidence does not exist") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail="retained presentation evidence is malformed") from exc


app = create_app()
