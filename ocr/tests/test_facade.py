import base64

import httpx
from conftest import Fake
from ocr.api import create_app
from ocr.config import Settings


async def test_facade_and_rejections(png):
    app = create_app(Settings(token="test"), Fake())
    headers = {"Authorization": "Bearer test"}
    body = {
        "model": "ocr-cpu",
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/png;base64," + base64.b64encode(png).decode()
                        },
                    }
                ],
            }
        ],
    }
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        r = await client.post("/v1/chat/completions", headers=headers, json=body)
        assert r.status_code == 200, r.text
        assert r.json()["choices"][0]["message"]["content"]
        assert "usage" not in r.json() and r.json()["ocr"]["sensitivity"] == "D2"
        for extra in ({"stream": True}, {"tools": []}, {"model": "alias-ocr"}):
            assert (
                await client.post("/v1/chat/completions", headers=headers, json={**body, **extra})
            ).status_code == 422
        body["messages"][0]["content"][0]["image_url"]["url"] = (
            "https://unapproved.invalid/image.png"
        )
        assert (
            await client.post("/v1/chat/completions", headers=headers, json=body)
        ).status_code == 422
