"""Final review I1: DNS rebinding / cross-site requests against a no-token loopback server."""
from fastapi.testclient import TestClient

from aidoc.server.app import create_app


def test_foreign_host_rejected_without_token(client):
    r = client.get("/api/system", headers={"Host": "evil.example:8765"})
    assert r.status_code == 403 and r.json()["error"] == "bad_host"
    assert client.get("/api/system", headers={"Host": "localhost:8765"}).status_code == 200
    assert client.get("/api/system", headers={"Host": "127.0.0.1"}).status_code == 200
    assert client.get("/api/system", headers={"Host": "[::1]:8765"}).status_code == 200
    assert client.get("/api/system", headers={"Host": "127.0.0.2:8765"}).status_code == 200
    assert client.get("/api/system", headers={"Host": "127.evil.example:8765"}).status_code == 403


def test_cross_origin_state_change_rejected(client):
    r = client.post("/api/queue/pause", headers={"Origin": "http://evil.example"})
    assert r.status_code == 403 and r.json()["error"] == "cross_origin"
    r = client.post("/api/queue/pause", headers={"Origin": "http://127.0.0.1:8765", "Host": "127.0.0.1:8765"})
    assert r.status_code == 200
    assert client.post("/api/queue/resume").status_code == 200            # no Origin (curl, CLI): allowed
    assert client.get("/api/system", headers={"Origin": "http://evil.example"}).status_code == 200   # safe method


def test_token_server_accepts_any_host_with_token(token_ctx):
    with TestClient(create_app(token_ctx), base_url="http://192.168.1.20:8765") as c:
        assert c.get("/api/system", headers={"Authorization": "Bearer s3cret"}).status_code == 200
        assert c.get("/api/system").status_code == 401
