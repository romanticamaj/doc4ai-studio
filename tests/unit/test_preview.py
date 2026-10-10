"""Office preview via LibreOffice (aidoc.preview): soffice detection, command construction, cache key, manager."""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

import pytest

from aidoc import preview as P

FAKE = Path(__file__).resolve().parents[1] / "fakes" / "fake_soffice.py"


# ---- detection

def test_office_extensions():
    for name in ("a.docx", "B.DOC", "c.pptx", "d.ppt", "e.xlsx", "f.xls", "g.odt", "h.odp", "i.ods", "j.rtf"):
        assert P.is_office(name), name
    for name in ("a.pdf", "b.png", "c.html", "d.epub", "e.csv", "noext", "x.docx.pdf"):
        assert not P.is_office(name), name


def test_configured_path_wins(tmp_path):
    exe = tmp_path / "custom" / "soffice.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"")
    assert P.find_soffice(str(exe), which=lambda _n: None, candidates=[]) == exe


def test_configured_path_missing_is_not_found_even_if_on_path(tmp_path):
    # an explicit setting that is wrong must not silently run some other binary
    other = tmp_path / "soffice"
    other.write_bytes(b"")
    assert P.find_soffice(str(tmp_path / "nope.exe"), which=lambda _n: str(other), candidates=[]) is None


def test_path_lookup_then_common_install_dirs(tmp_path):
    on_path = tmp_path / "bin" / "soffice"
    on_path.parent.mkdir()
    on_path.write_bytes(b"")
    assert P.find_soffice("", which=lambda n: str(on_path) if n == "soffice" else None, candidates=[]) == on_path
    installed = tmp_path / "LibreOffice" / "program" / "soffice.exe"
    installed.parent.mkdir(parents=True)
    installed.write_bytes(b"")
    missing = tmp_path / "Nope" / "soffice.exe"
    assert P.find_soffice("", which=lambda _n: None, candidates=[missing, installed]) == installed
    assert P.find_soffice("", which=lambda _n: None, candidates=[missing]) is None


def test_default_candidates_cover_windows_program_files():
    names = [str(c).replace("\\", "/") for c in P.default_candidates()]
    if sys.platform == "win32":
        assert any(n.endswith("LibreOffice/program/soffice.exe") for n in names)
    else:
        assert any(n.endswith("soffice") for n in names)


def test_version_from_version_ini(tmp_path):
    prog = tmp_path / "program"
    prog.mkdir()
    exe = prog / "soffice.exe"
    exe.write_bytes(b"")
    (prog / "version.ini").write_text("[Version]\nAllLanguages=en-US\nMsiProductVersion=25.8.1.1\n"
                                      "ProductMajor=258\n", encoding="utf-8")
    assert P.soffice_version(exe) == "25.8.1.1"


def test_version_from_the_registry_when_version_ini_has_none(tmp_path):
    # LibreOffice 26.x on Windows: version.ini carries only buildid; the product version is in main.xcd
    prog = tmp_path / "program"
    prog.mkdir()
    exe = prog / "soffice.exe"
    exe.write_bytes(b"")
    (prog / "version.ini").write_text("[Version]\nbuildid=bce0998a\nVendor=The Document Foundation\n",
                                      encoding="utf-8")
    reg = tmp_path / "share" / "registry"
    reg.mkdir(parents=True)
    (reg / "main.xcd").write_text('<x><prop oor:name="ooSetupVersion"><value>26.8</value></prop>'
                                  '<prop oor:name="ooSetupVersionAboutBox"><value>26.8.0.3</value></prop></x>',
                                  encoding="utf-8")
    assert P.soffice_version(exe) == "26.8.0.3"


def test_version_unknown_without_version_ini(tmp_path):
    exe = tmp_path / "soffice.exe"
    exe.write_bytes(b"")
    assert P.soffice_version(exe, run_version=False) is None


# ---- command

def test_command_is_headless_isolated_and_never_restores(tmp_path):
    exe = Path("C:/LO/program/soffice.exe")
    src = tmp_path / "w" / "src.docx"
    out = tmp_path / "w" / "out"
    profile = tmp_path / "profile"
    cmd = P.build_command([str(exe)], src, out, profile)
    assert cmd[0] == str(exe) and cmd[-1] == str(src)
    for flag in ("--headless", "--norestore", "--nologo", "--nodefault", "--nolockcheck", "--invisible"):
        assert flag in cmd, flag
    i = cmd.index("--convert-to")
    assert cmd[i + 1] == "pdf"
    assert cmd[cmd.index("--outdir") + 1] == str(out)
    env = [a for a in cmd if a.startswith("-env:UserInstallation=")]
    assert env == [f"-env:UserInstallation={profile.resolve().as_uri()}"]
    assert env[0].startswith("-env:UserInstallation=file:///")


def test_macro_policy_is_written_into_the_profile(tmp_path):
    P.write_profile_policy(tmp_path / "profile")
    xcu = (tmp_path / "profile" / "user" / "registrymodifications.xcu").read_text(encoding="utf-8")
    assert 'oor:name="MacroSecurityLevel"' in xcu and "<value>3</value>" in xcu
    assert 'oor:name="DisableMacrosExecution"' in xcu
    # rewriting keeps one copy of each setting
    P.write_profile_policy(tmp_path / "profile")
    xcu2 = (tmp_path / "profile" / "user" / "registrymodifications.xcu").read_text(encoding="utf-8")
    assert xcu2.count('oor:name="DisableMacrosExecution"') == 1


# ---- cache

def test_cache_key_is_the_source_sha256(tmp_path):
    sha = hashlib.sha256(b"x").hexdigest()
    cache = P.PreviewCache(tmp_path)
    assert cache.pdf_path(sha) == tmp_path / "previews" / f"{sha}.pdf"
    assert cache.meta_path(sha) == tmp_path / "previews" / f"{sha}.json"
    with pytest.raises(ValueError):
        cache.pdf_path("../../etc/passwd")
    with pytest.raises(ValueError):
        cache.pdf_path(sha.upper()[:10])


# ---- manager (fake soffice: a Python script standing in for soffice.exe)

def _doc(tmp_path, name="sample.docx", data=b"PK fake office bytes"):
    src = tmp_path / "src" / name
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(data)
    return {"id": "doc1", "sha256": hashlib.sha256(data).hexdigest(), "source_path": str(src),
            "work_copy_path": None}


def _manager(tmp_path, mode="ok", events=None, **kw):
    log = tmp_path / "soffice_calls.jsonl"
    cfg = P.PreviewSettings(**kw)
    m = P.PreviewManager(tmp_path / "data", cfg, exe=[sys.executable, str(FAKE)],
                         env={"FAKE_SOFFICE_MODE": mode, "FAKE_SOFFICE_LOG": str(log)},
                         publish=(lambda payload: events.append(payload)) if events is not None else None)
    return m, log


def _wait(m, doc, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = m.status(doc)
        if st["status"] in ("ready", "failed"):
            return st
        time.sleep(0.05)
    raise AssertionError(f"preview still {m.status(doc)}")


def test_status_none_until_requested_then_ready_with_pages(tmp_path):
    events = []
    m, log = _manager(tmp_path, events=events)
    try:
        doc = _doc(tmp_path)
        assert m.status(doc) == {"status": "none"}
        assert m.request(doc)["status"] == "pending"
        st = _wait(m, doc)
        assert st == {"status": "ready", "pages": 2}
        pdf = m.pdf_path(doc)
        assert pdf is not None and pdf.read_bytes().startswith(b"%PDF-")
        assert events[-1] == {"document_id": "doc1", "status": "ready", "pages": 2}
        call = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
        assert call["src"].endswith(".docx") and Path(call["src"]).parent != Path(doc["source_path"]).parent
        assert any(a.startswith("-env:UserInstallation=file:///") for a in call["argv"])
        # a second request is a cache hit: no new soffice run
        assert m.request(doc) == {"status": "ready", "pages": 2}
        assert len(log.read_text(encoding="utf-8").splitlines()) == 1
    finally:
        m.stop()


def test_temp_dirs_are_cleaned_up(tmp_path):
    m, _ = _manager(tmp_path)
    try:
        doc = _doc(tmp_path)
        m.request(doc)
        _wait(m, doc)
        tmp = tmp_path / "data" / "previews" / ".tmp"
        assert not tmp.exists() or not any(tmp.iterdir())
    finally:
        m.stop()


def test_conversion_failure_is_reported_and_cached(tmp_path):
    m, log = _manager(tmp_path, mode="fail")
    try:
        doc = _doc(tmp_path)
        m.request(doc)
        st = _wait(m, doc)
        assert st["status"] == "failed" and st["reason"] == "conversion_failed"
        m.request(doc)                                       # failed is sticky: no retry storm
        time.sleep(0.2)
        assert len(log.read_text(encoding="utf-8").splitlines()) == 1
        assert m.request(doc, retry=True)["status"] == "pending"
        assert _wait(m, doc)["status"] == "failed"
        assert len(log.read_text(encoding="utf-8").splitlines()) == 2
    finally:
        m.stop()


def test_timeout_kills_soffice(tmp_path):
    m, _ = _manager(tmp_path, mode="hang", timeout_s=1)
    try:
        doc = _doc(tmp_path)
        t0 = time.time()
        m.request(doc)
        st = _wait(m, doc)
        assert st == {"status": "failed", "reason": "timeout"} and time.time() - t0 < 15
    finally:
        m.stop()


def test_too_large_is_refused_without_running(tmp_path):
    m, log = _manager(tmp_path, max_mb=1)
    try:
        doc = _doc(tmp_path, data=b"x" * (1024 * 1024 + 1))
        st = m.request(doc)
        st = st if st["status"] == "failed" else _wait(m, doc)
        assert st["status"] == "failed" and st["reason"] == "too_large" and not log.exists()
    finally:
        m.stop()


def test_source_changed_is_not_converted(tmp_path):
    m, log = _manager(tmp_path)
    try:
        doc = _doc(tmp_path)
        Path(doc["source_path"]).write_bytes(b"something else")
        m.request(doc)
        st = _wait(m, doc)
        assert st["status"] == "failed" and st["reason"] == "source_changed" and not log.exists()
    finally:
        m.stop()


def test_missing_soffice_and_source(tmp_path):
    m = P.PreviewManager(tmp_path / "data", P.PreviewSettings(), exe=None, locate=lambda: None)
    try:
        doc = _doc(tmp_path)
        assert m.status(doc) == {"status": "failed", "reason": "soffice_missing"}
        assert m.request(doc) == {"status": "failed", "reason": "soffice_missing"}
    finally:
        m.stop()
    m, _ = _manager(tmp_path)
    try:
        doc = _doc(tmp_path)
        Path(doc["source_path"]).unlink()
        st = m.request(doc)
        assert st == {"status": "failed", "reason": "source_missing"}
    finally:
        m.stop()


def test_disabled(tmp_path):
    m, _ = _manager(tmp_path, enabled=False)
    try:
        doc = _doc(tmp_path)
        assert m.request(doc) == {"status": "failed", "reason": "disabled"}
    finally:
        m.stop()


def test_one_conversion_at_a_time(tmp_path):
    m, log = _manager(tmp_path, mode="slow")
    try:
        docs = [dict(_doc(tmp_path, f"d{i}.docx", f"bytes {i}".encode()), id=f"doc{i}") for i in range(3)]
        for d in docs:
            m.request(d)
        for d in docs:
            assert _wait(m, d)["status"] == "ready"
        calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        spans = sorted((c["start"], c["end"]) for c in calls)
        assert all(spans[i][1] <= spans[i + 1][0] for i in range(len(spans) - 1)), spans
    finally:
        m.stop()


def test_purge_removes_previews_of_unknown_documents(tmp_path):
    cache = P.PreviewCache(tmp_path)
    keep, drop = hashlib.sha256(b"a").hexdigest(), hashlib.sha256(b"b").hexdigest()
    for sha in (keep, drop):
        cache.pdf_path(sha).parent.mkdir(parents=True, exist_ok=True)
        cache.pdf_path(sha).write_bytes(b"%PDF-1.4")
        cache.meta_path(sha).write_text("{}", encoding="utf-8")
    assert cache.purge(known={keep}) == 1
    assert cache.pdf_path(keep).exists() and not cache.pdf_path(drop).exists() and not cache.meta_path(drop).exists()


def test_fake_script_exists():
    assert FAKE.is_file() and shutil.which(sys.executable)
