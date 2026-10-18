import os

os.environ["OCR_CPU_AUTH_TOKEN"] = "test-token"

import pytest
from fastapi.testclient import TestClient
from ocr.config import Settings
from ocr.worker_api import create_app


class MockBackend:
    class Health:
        ready = True
    async def health(self):
        return self.Health()
    async def capabilities(self):
        return {"models": []}
    async def recognize(self, p, opts):
        return {}

@pytest.fixture(scope="module")
def client():
    settings = Settings()
    settings.worker_token = "test-token"
    app = create_app(settings)
    with TestClient(app) as c:
        app.state.backend = MockBackend()
        yield c

def test_health_public(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

def test_ready_unauthorized(client):
    response = client.get("/ready")
    assert response.status_code == 401

def test_v1_models_authorized(client):
    response = client.get("/v1/models", headers={"Authorization": "Bearer test-token"})
    assert response.status_code == 200
    assert response.json()["data"][0]["id"] == "ocr-cpu"

def test_v1_chat_completions_invalid_model(client):
    req = {
        "model": "gpt-4",
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="},
                    }
                ],
            }
        ],
    }
    response = client.post(
        "/v1/chat/completions", headers={"Authorization": "Bearer test-token"}, json=req
    )
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"

def test_v1_chat_completions_invalid_url(client):
    req = {
        "model": "ocr-cpu",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": "https://example.com/image.png"}}
                ],
            }
        ],
    }
    response = client.post(
        "/v1/chat/completions", headers={"Authorization": "Bearer test-token"}, json=req
    )
    assert response.status_code == 422
    assert response.json()["code"] == "only_image_data_urls_supported"

def test_v1_ocr_page_wrong_sensitivity(client):
    files = {"file": ("test.png", b"fake-image-data", "image/png")}
    data = {"sensitivity": "D1"}
    response = client.post(
        "/v1/ocr/page", headers={"Authorization": "Bearer test-token"}, files=files, data=data
    )
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_worker_options"
