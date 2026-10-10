import io

from tests.compat.test_sync import PDF


def test_ua_identity():
    from aidoc.mcp.calllog import ua_identity
    assert ua_identity("python-requests/2.32.3") == ("python-requests", "2.32.3")
    assert ua_identity("Go-http-client/1.1") == ("Go-http-client", "1.1")
    assert ua_identity("curl/8.9.1 (x86_64)") == ("curl", "8.9.1")
    assert ua_identity("Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36") == ("Mozilla", "5.0")
    assert ua_identity(None) == ("unknown", None) and ua_identity("") == ("unknown", None)
    assert len(ua_identity("x" * 500)[0]) <= 100


def test_compat_calls_show_up_in_clients_tokens_and_status(workers):
    env = workers
    raw, tid = env.issue(name="openwebui")
    env.client.post("/v1/convert/file", files={"files": ("a.pdf", io.BytesIO(PDF), "application/pdf")},
                    headers={"X-Api-Key": raw, "User-Agent": "python-requests/2.32.3"})
    clients = env.client.get("/api/mcp/clients").json()["clients"]
    me = [c for c in clients if c["token_id"] == tid]
    assert me and me[0]["client_name"] == "python-requests" and me[0]["protocol_version"] == "docling-serve-v1"
    tok = next(t for t in env.client.get("/api/mcp/tokens").json()["tokens"] if t["id"] == tid)
    assert tok["calls_24h"] >= 1 and tok["last_client"] == "python-requests/2.32.3"
    status = env.client.get("/api/mcp/status").json()
    assert status["compat"]["enabled"] is True and status["compat"]["base_url"].endswith(":8765") and status["compat"]["calls_24h"] >= 1
    assert not status["compat"]["base_url"].endswith("/mcp")
    calls = env.client.get("/api/mcp/calls", params={"tool": "convert_file"}).json()["calls"]
    assert calls and calls[0]["method"] == "compat/docling" and calls[0]["token_name"] == "openwebui"
    jobs = env.client.get("/api/jobs").json()["jobs"]
    assert any(j["origin"] == "compat" for j in jobs)


def test_compat_calls_24h_counts_only_compat(compat_env):
    compat_env.client.get("/v1/capabilities")
    compat_env.ctx.extras["mcp"].recorder.record(status="ok", method="tools/call", tool_name="read_document")
    s = compat_env.client.get("/api/mcp/status").json()
    assert s["compat"]["calls_24h"] == 1 and s["calls_24h"] == 2


def test_web_jobs_cannot_claim_compat_origin(client):
    r = client.post("/api/jobs", json={"inputs": [{"upload_id": "u1"}], "origin": "compat"})
    assert r.status_code == 422 and "origin" in r.text


def test_calls_can_be_filtered_by_method(compat_env):
    compat_env.client.get("/v1/capabilities")
    compat_env.ctx.extras["mcp"].recorder.record(status="ok", method="tools/call", tool_name="read_document")
    calls = compat_env.client.get("/api/mcp/calls", params={"method": "compat/docling"}).json()["calls"]
    assert [c["tool_name"] for c in calls] == ["capabilities"]
