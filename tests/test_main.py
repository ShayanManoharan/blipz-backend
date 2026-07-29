from fastapi.testclient import TestClient

from app.main import app


def test_root():
    with TestClient(app) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {"message": "Blipz API is running!"}


def test_games_test_route():
    with TestClient(app) as client:
        response = client.get("/games/test")
    assert response.status_code == 200
    assert response.json() == {"message": "Games router is working"}
