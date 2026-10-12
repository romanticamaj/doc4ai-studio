"""422 instead of 500 for hostile inputs, and no reading past the closing boundary (verifier items 4, 5)."""
import io
import json

import anyio
import pytest

from tests.compat.test_sync import PDF


@pytest.mark.parametrize("value", ["inf", "nan", "1e309", "Infinity", "NaN", "1e400", "-5"])
def test_document_timeout_must_be_a_finite_positive_number(compat_env, value):
    compat_env.ctx.config.compat.sync_timeout_s = 1         # no worker runs here: a wrongly accepted request times out
    raw, _ = compat_env.issue()
    r = compat_env.client.post("/v1/convert/file", files={"files": ("a.pdf", io.BytesIO(PDF))},
                               data={"document_timeout": value}, headers={"X-Api-Key": raw})
    assert r.status_code == 422, (value, r.status_code, r.text[:200])
    body = '{"options": {"document_timeout": %s}, "sources": [{"kind": "file", "filename": "a.pdf", "base64_string": "JVBERg=="}]}'
    num = {"inf": "1e999", "nan": "NaN", "Infinity": "Infinity", "NaN": "NaN"}.get(value, value)
    r = compat_env.client.post("/v1/convert/source", content=(body % num).encode(),
                               headers={"X-Api-Key": raw, "Content-Type": "application/json"})
    assert r.status_code == 422, (value, r.status_code, r.text[:200])


def test_deeply_nested_json_is_422(compat_env):
    compat_env.ctx.config.compat.sync_timeout_s = 1
    raw, _ = compat_env.issue()
    for depth in (100_000, 100):
        body = b'{"options": ' + b"[" * depth + b"]" * depth + b"}"
        r = compat_env.client.post("/v1/convert/source", content=body,
                                   headers={"X-Api-Key": raw, "Content-Type": "application/json"})
        assert r.status_code == 422, (depth, r.status_code)


@pytest.mark.parametrize("boundary", ["x" * 300, "x" * 71, 'bad"quote'])
def test_bad_multipart_boundary_is_422(compat_env, boundary):
    compat_env.ctx.config.compat.sync_timeout_s = 1
    raw, _ = compat_env.issue()
    body = f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; filename=\"a.pdf\"\r\n\r\n".encode() + PDF + \
        f"\r\n--{boundary}--\r\n".encode()
    r = compat_env.client.post("/v1/convert/file", content=body, headers={
        "X-Api-Key": raw, "Content-Type": f'multipart/form-data; boundary="{boundary}"'})
    assert r.status_code == 422, r.text[:200]
    assert not list(compat_env.ctx.uploads.dir.glob("*.part"))


def test_reading_stops_at_the_closing_boundary(compat_env):
    """Verifier item 5: an endless epilogue after the closing boundary is not read."""
    from aidoc.compat.auth import CompatCall
    from aidoc.compat.jobs import stream_upload
    boundary = "edge5"
    consumed = {"n": 0}

    from types import SimpleNamespace

    class FakeRequest:
        def __init__(self):
            self.headers = {"content-type": f"multipart/form-data; boundary={boundary}"}
            self.app = SimpleNamespace(state=SimpleNamespace(ctx=compat_env.ctx))

        async def stream(self):
            yield f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; filename=\"a.pdf\"\r\n\r\n".encode()
            yield PDF + f"\r\n--{boundary}--\r\n".encode()
            for _ in range(10_000):                     # 640 MB of epilogue if it were all read
                consumed["n"] += 1
                yield b"e" * 65536

    call = CompatCall(principal=None, ts=0.0, started=0.0, protocol="docling-serve-v1", ip=None, ua=None)
    up = anyio.run(lambda: stream_upload(FakeRequest(), call, max_mb=1))
    assert up.size == len(PDF) and consumed["n"] <= 1
    up.part_path.unlink()
    assert json.dumps(up.fields) == "{}"
