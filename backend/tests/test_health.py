from fastapi.testclient import TestClient


def test_health_is_local_ready(app) -> None:
    response = TestClient(app).get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "mode": "local"}
