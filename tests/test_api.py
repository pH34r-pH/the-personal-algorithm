from datetime import UTC, datetime

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
