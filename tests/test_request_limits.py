from fastapi.testclient import TestClient

from app.main import app
from app.middleware import MAX_REQUEST_BODY_BYTES


client = TestClient(app)


def test_rejects_request_bodies_over_configured_limit():
    response = client.post(
        "/api/metadata",
        content=b"x" * (MAX_REQUEST_BODY_BYTES + 1),
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 413
    assert "limit" in response.json()["detail"]
