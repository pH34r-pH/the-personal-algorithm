from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from personal_algorithm.api import create_app
from personal_algorithm.store import Store

NOW = datetime(2026, 1, 1, 12, tzinfo=UTC).isoformat()


def candidate_payload() -> dict:
    return {
        "id": "fixture:api-1",
        "source": "fixture",
        "source_id": "api-1",
        "canonical_url": "https://example.invalid/api-1",
        "title": "Inspectable recommendations",
        "discovered_at": NOW,
        "published_at": NOW,
        "content_type": "paper",
        "topics": ["recommenders"],
        "provenance": {"kind": "test"},
    }


def test_health_and_validation_boundary():
    client = TestClient(create_app(Store()))

    assert client.get("/health").json() == {"status": "ok"}
    response = client.post("/candidates", json={"title": "incomplete"})
    assert response.status_code == 422
    assert "discovered_at" in response.json()["detail"]


def test_rank_explains_and_persists_then_accepts_feedback():
    store = Store()
    client = TestClient(create_app(store))
    candidate = candidate_payload()

    response = client.post(
        "/rank",
        json={
            "candidate": candidate,
            "policy": {
                "version": "api-test-v1",
                "weights": {"topic_affinity": 0.8, "recency": 0.2},
                "topic_affinity": {"recommenders": 1.0},
            },
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["candidate_id"] == candidate["id"]
    assert body["score"] == body["explanation"]["final_score"]
    assert len(body["explanation"]["contributions"]) == 3

    feedback = client.post(
        "/interactions",
        json={
            "candidate_id": candidate["id"],
            "kind": "learned",
            "occurred_at": NOW,
            "explicit": True,
        },
    )
    assert feedback.status_code == 201
    assert store.connection.execute("SELECT COUNT(*) FROM rankings").fetchone()[0] == 1
    assert store.connection.execute("SELECT COUNT(*) FROM interactions").fetchone()[0] == 1


def test_interaction_rejects_unknown_candidate():
    client = TestClient(create_app(Store()))
    response = client.post(
        "/interactions",
        json={
            "candidate_id": "missing",
            "kind": "saved",
            "occurred_at": NOW,
        },
    )
    assert response.status_code == 409


def test_run_order_exposure_and_feedback_are_distinct_local_records(tmp_path):
    store = Store(tmp_path / "ranking.sqlite3")
    client = TestClient(create_app(store))
    first = candidate_payload()
    second = {**first, "id": "fixture:api-2", "source_id": "api-2"}
    policy = {"version": "fixture", "weights": {"topic_affinity": -1, "recency": 0}}
    response = client.post("/ranking-runs", json={"candidates": [second, first], "policy": policy})
    assert response.status_code == 201, response.text
    run = response.json()
    assert [item["candidate_id"] for item in run["candidates"]] == [first["id"], second["id"]]
    assert [item["position"] for item in run["candidates"]] == [1, 2]
    assert run["ranker"] == "reference-v0" and not run["context_sha256"]
    for item in client.get(f"/ranking-runs/{run['id']}").json()["candidates"]:
        explanation = item["explanation"]
        total = sum(value["contribution"] for value in explanation["contributions"])
        assert item["score"] == pytest.approx(total, rel=1e-12, abs=1e-12)
        assert item["score"] == explanation["final_score"]
    assert store.connection.execute("SELECT COUNT(*) FROM impressions").fetchone()[0] == 0

    exposed = datetime.fromisoformat(run["evaluated_at"]) + timedelta(seconds=1)
    presentation = {
        "id": "presentation", "run_id": run["id"], "candidate_ids": [second["id"], first["id"]],
        "presented_at": exposed.isoformat(),
    }
    result = client.post("/impressions", json=presentation)
    assert result.status_code == 201 and result.json() == presentation
    assert client.post("/impressions", json=presentation).json() == presentation
    changed = {**presentation, "candidate_ids": [first["id"]]}
    assert client.post("/impressions", json=changed).status_code == 409
    persisted = store.get_impression("presentation")
    assert persisted.candidate_ids == (second["id"], first["id"])

    interaction = {
        "candidate_id": second["id"], "kind": "saved",
        "occurred_at": exposed.isoformat(), "impression_id": persisted.id,
    }
    too_early = {**interaction, "occurred_at": run["evaluated_at"]}
    assert client.post("/interactions", json=too_early).status_code == 409
    assert client.post("/interactions", json=interaction).status_code == 201
    link = store.connection.execute(
        """SELECT exposure.impression_id, interaction.candidate_id
           FROM interaction_impressions AS exposure
           JOIN interactions AS interaction ON interaction.id = exposure.interaction_id"""
    ).fetchone()
    assert link == ("presentation", second["id"])
    assert store.connection.execute("SELECT COUNT(*) FROM impressions").fetchone()[0] == 1
    assert store.connection.execute("SELECT COUNT(*) FROM interactions").fetchone()[0] == 1
