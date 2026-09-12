from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from waterfallhunter.core.request_body_limit import RequestBodyLimitMiddleware


def test_ingress_limit_counts_streamed_chunks_before_body_parsing() -> None:
    app = FastAPI()
    app.add_middleware(
        RequestBodyLimitMiddleware,
        path="/limited",
        maximum_bytes=10,
    )

    @app.post("/limited")
    async def limited(request: Request):
        return {"size": len(await request.body())}

    client = TestClient(app)
    response = client.post(
        "/limited",
        content=(chunk for chunk in (b"123456", b"78901")),
        headers={"content-type": "application/octet-stream"},
    )

    assert response.status_code == 413
    assert response.json()["detail"] == "request body exceeds configured limit"
