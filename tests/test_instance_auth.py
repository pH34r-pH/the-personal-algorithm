from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from personal_algorithm.instance_auth import InstanceAuth
from personal_algorithm.private_api import protect


def _client():
    app = FastAPI()
    router = APIRouter(prefix="/private")

    @router.get("/hello")
    def hello():
        return {"ok": True}

    app.include_router(protect(router, InstanceAuth(owner_subject="owner-123")))
    return TestClient(app)


def test_private_route_requires_identity():
    client = _client()
    response = client.get("/private/hello")
    assert response.status_code == 401


def test_private_route_rejects_other_identity():
    client = _client()
    response = client.get(
        "/private/hello",
        headers={"x-ms-client-principal-name": "someone-else"},
    )
    assert response.status_code == 403


def test_private_route_accepts_owner():
    client = _client()
    response = client.get(
        "/private/hello",
        headers={"x-ms-client-principal-name": "owner-123"},
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}
