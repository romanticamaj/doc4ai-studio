"""aidoc sources status|backfill (spec 2026-10-29 §7.4, §10.3; plan Task 9): exit codes, JSON, forwarding."""
from __future__ import annotations

import json
import os
from pathlib import Path

import httpx

from aidoc.cli import main
from aidoc.client import ServerClient
from aidoc.source_copy import backfill
from tests.unit.test_backfill import lib  # noqa: F401  the 8-document library of A6
from tests.unit.test_pipeline_source_copy import Lib


def test_dry_run_prints_totals_and_exits_3(lib, capsys):  # noqa: F811
    tree = sorted(p for p in Path(lib.root).rglob("*") if p.is_file())
    rc = main(["sources", "backfill", "--dry-run"])
    out = capsys.readouterr().out
    assert rc == 3                                            # missing originals / sha mismatch
    assert "dry run" in out and str(lib.cfg.source_root()) in out
    assert "copied 3   already present 1   missing original 2   sha mismatch 1   unreadable 0   failed 0   " \
           "skipped (orphaned) 1" in out
    assert "missing original:" in out and "(changed since conversion)" in out and str(lib.srcs["d7"]) in out
    assert sorted(p for p in Path(lib.root).rglob("*") if p.is_file() and p.name != "aidoc.lock") == \
        [p for p in tree if p.name != "aidoc.lock"]


def test_json_equals_the_report_shape(lib, capsys):  # noqa: F811
    assert main(["sources", "backfill", "--dry-run", "--json"]) == 3
    got = json.loads(capsys.readouterr().out)
    ref = backfill(lib.store, lib.cfg, dry_run=True)
    assert set(got) == set(ref) and got["totals"] == ref["totals"]
    assert [(d["id"], d["outcome"]) for d in got["documents"]] == [(d["id"], d["outcome"]) for d in ref["documents"]]


def test_status(lib, capsys):  # noqa: F811
    assert main(["sources", "status", "--json"]) == 0
    s = json.loads(capsys.readouterr().out)
    assert s["documents"] == 7 and s["source_dir"] == str(lib.cfg.source_root())
    assert main(["sources", "status"]) == 0
    assert "not copied" in capsys.readouterr().out


def test_forwards_to_a_running_server(tmp_root, monkeypatch, capsys):
    seen = []

    def reply(request):
        seen.append((request.method, request.url.path, json.loads(request.content or b"{}")))
        return httpx.Response(200, json={"dry_run": True, "source_dir": "D:\\doc4ai\\source", "totals": {
            "copied": 1, "already_present": 0, "missing_original": 0, "sha_mismatch": 0, "unreadable": 0,
            "failed": 0, "skipped_orphaned": 0}, "documents": [], "unreferenced_files": 0, "workspace": "default"})
    monkeypatch.setattr("aidoc.client.find_server",
                        lambda cfg, token=None: ServerClient("http://x", None, transport=httpx.MockTransport(reply)))
    assert main(["sources", "backfill", "--dry-run"]) == 0
    assert seen == [("POST", "/api/sources/backfill", {"dry_run": True})]


def test_exit_1_when_a_document_is_unreadable(tmp_root, fixtures, monkeypatch, capsys):
    from aidoc import source_copy
    lb = Lib(tmp_root, fixtures, monkeypatch)
    _st, t = lb.convert(lb.place("u.pdf"))
    d = lb.doc(t)
    os.unlink(d["source_copy_path"])
    lb.store.set_source_copy(d["id"], path=None, status=None, error=None)
    lb.store.close()
    monkeypatch.setattr(source_copy, "_check", lambda p, sha: "unreadable")
    assert main(["sources", "backfill"]) == 1
    assert "unreadable:" in capsys.readouterr().out


def test_exit_0_when_everything_is_copied(tmp_root, fixtures, monkeypatch, capsys):
    lb = Lib(tmp_root, fixtures, monkeypatch)
    lb.convert(lb.place("ok.pdf"))
    lb.store.close()
    assert main(["sources", "backfill"]) == 0
    assert "already present 1" in capsys.readouterr().out


def test_no_subcommand_is_usage(tmp_root, capsys):
    assert main(["sources"]) == 4
    assert "usage: aidoc sources" in capsys.readouterr().err


def test_serverless_dry_run_writes_nothing(tmp_root, fixtures, monkeypatch, capsys):
    """Verifier V3: the in-process --dry-run ran startup recovery first, which purged an expired work copy."""
    import time

    lb = Lib(tmp_root, fixtures, monkeypatch)
    _st, t = lb.convert(lb.place("dry.pdf"))
    d = lb.doc(t)
    os.unlink(d["source_copy_path"])
    lb.store.set_source_copy(d["id"], path=None, status=None, error=None)
    lb.store.update_document(d["id"], work_copy_expires_at=time.time() - 10)
    rows = lambda: [tuple(r) for r in lb.store.con.execute("SELECT * FROM documents ORDER BY id")]  # noqa: E731
    before_rows = rows()

    def tree():
        return sorted((p.relative_to(tmp_root).as_posix(), p.stat().st_size, p.stat().st_mtime_ns)
                      for p in tmp_root.rglob("*") if p.is_file() and not p.name.startswith("aidoc.db"))
    before = tree()
    assert main(["sources", "backfill", "--dry-run"]) == 0
    assert "dry run" in capsys.readouterr().out
    assert tree() == before and rows() == before_rows
    assert Path(d["work_copy_path"]).is_dir()                    # the expired work copy is still there
    lb.store.close()
