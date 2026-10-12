"""Multipart edge cases from the final review (findings 4, 5): truncated bodies, no Content-Length."""
from tests.compat.conftest import last_call
from tests.compat.test_sync import PDF

BOUNDARY = "doc4aiedge"


def _head(name="a.pdf") -> bytes:
    return (f"--{BOUNDARY}\r\nContent-Disposition: form-data; name=\"files\"; filename=\"{name}\"\r\n"
            f"Content-Type: application/pdf\r\n\r\n").encode()


def test_truncated_multipart_body_is_422_and_leaves_no_part(compat_env):
    compat_env.ctx.config.compat.sync_timeout_s = 1         # no worker runs here: a wrongly accepted file times out
    raw, _ = compat_env.issue()
    body = _head() + PDF                                    # no closing boundary: the upload was cut off
    r = compat_env.client.post("/v1/convert/file", content=body, headers={
        "X-Api-Key": raw, "Content-Type": f"multipart/form-data; boundary={BOUNDARY}"})
    assert r.status_code == 422 and "incomplete" in r.json()["detail"]
    assert not list(compat_env.ctx.uploads.dir.glob("*.part"))
    assert not compat_env.ctx.store.list_jobs()


def test_oversized_upload_without_content_length_is_cut(compat_env):
    """Review focus 1: the byte cap does not rely on a declared Content-Length (chunked transfer)."""
    env = compat_env
    env.ctx.config.compat.max_file_mb = 1

    def gen():
        yield _head()
        yield PDF
        for _ in range(64):
            yield b"\0" * 65536                             # 4 MB in total, never declared
        yield f"\r\n--{BOUNDARY}--\r\n".encode()
    raw, _ = env.issue()
    r = env.client.post("/v1/convert/file", content=gen(), headers={
        "X-Api-Key": raw, "Content-Type": f"multipart/form-data; boundary={BOUNDARY}"})
    assert r.status_code == 413 and not list(env.ctx.uploads.dir.glob("*.part"))
    assert last_call(env.ctx)["error_code"] == "file_too_large"
