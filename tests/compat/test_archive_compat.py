import io

from tests.unit.test_archive_limits import bomb_docx


def test_compat_answers_the_bomb_as_an_input_failure(workers, tmp_path):
    env = workers
    raw, _ = env.issue()
    data = bomb_docx(tmp_path / "bomb.docx", mb=50).read_bytes()
    r = env.client.post("/v1/convert/file", files={"files": ("bomb.docx", io.BytesIO(data))}, headers={"X-Api-Key": raw})
    body = r.json()
    assert r.status_code == 200 and body["status"] == "failure"
    assert body["errors"][0]["error_message"].startswith("archive_too_large")
    assert body["errors"][0]["component_type"] == "user_input" and not env.ctx.store.list_jobs()
