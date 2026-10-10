"""Office preview: LibreOffice (soffice, headless) renders an Office source to PDF for the Document view's left
pane, so Word / PowerPoint / Excel / OpenDocument / RTF files get the same PDF viewer as PDFs.

- On demand: the first preview request of a document queues it (never at conversion time: a CLI batch of
  thousands of files pays nothing, documents converted before this feature work, and the job queue is untouched).
- One conversion at a time on a dedicated thread, with a timeout that kills soffice's whole process tree.
- Cached by the source's sha256 under data/previews/<sha256>.pdf (+ <sha256>.json: ready/failed, page count).
  The output dir is the conversion contract (and download.zip), so the preview does not live there; work copies
  expire after 7 days, so it does not live there either.
- Converts a private copy of the source (sha256-verified) in a temp dir, removed afterwards.
- Isolated LibreOffice user profile (-env:UserInstallation=…) so a desktop LibreOffice the user has open never
  locks it, seeded with a policy that disables macro execution and the loading of linked content.
"""
from __future__ import annotations

import configparser
import functools
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from aidoc import fsops
from aidoc.config import Preview
from aidoc.names import file_sha256
from aidoc.procs import kill_tree, popen_kwargs

OFFICE_EXTS = frozenset({".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls", ".odt", ".odp", ".ods", ".rtf"})
_SHA = re.compile(r"^[0-9a-f]{64}$")


def is_office(name: str) -> bool:
    return Path(str(name)).suffix.lower() in OFFICE_EXTS


# ---- locating soffice

def default_candidates() -> list[Path]:
    if sys.platform == "win32":
        out = []
        for var in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
            base = os.environ.get(var)
            if base:
                out.append(Path(base) / "LibreOffice" / "program" / "soffice.exe")
        out += [Path("C:/Program Files/LibreOffice/program/soffice.exe"),
                Path("C:/Program Files (x86)/LibreOffice/program/soffice.exe")]
        return list(dict.fromkeys(out))
    if sys.platform == "darwin":
        return [Path("/Applications/LibreOffice.app/Contents/MacOS/soffice")]
    return [Path("/usr/bin/soffice"), Path("/usr/local/bin/soffice"), Path("/usr/lib/libreoffice/program/soffice"),
            Path("/opt/libreoffice/program/soffice"), Path("/snap/bin/libreoffice")]


def find_soffice(configured: str = "", *, which: Callable[[str], str | None] = shutil.which,
                 candidates: list[Path] | None = None) -> Path | None:
    """`[preview] soffice_path` when set (and only that: a wrong setting must not quietly run another binary),
    else soffice on PATH, else the usual install locations."""
    if configured:
        p = Path(configured)
        return p if p.is_file() else None
    for name in ("soffice", "soffice.exe") if sys.platform == "win32" else ("soffice", "libreoffice"):
        hit = which(name)
        if hit and Path(hit).is_file():
            return Path(hit)
    for c in default_candidates() if candidates is None else candidates:
        if c.is_file():
            return c
    return None


def soffice_version(exe: Path, *, run_version: bool = True) -> str | None:
    """From program/version.ini (Windows, and Linux builds from TDF), else `soffice --version`."""
    for ini in (exe.parent / "version.ini", exe.parent / "versionrc"):
        if ini.is_file():
            cp = configparser.ConfigParser(interpolation=None, strict=False)
            try:
                cp.read_string(ini.read_text(encoding="utf-8", errors="replace"))
            except (configparser.Error, OSError):
                continue
            for section in cp.sections():
                for key in ("MsiProductVersion", "buildid_version", "ProductVersion"):
                    if cp.has_option(section, key):
                        return cp.get(section, key).strip() or None
    xcd = exe.parent.parent / "share" / "registry" / "main.xcd"     # LibreOffice 7.x+: product version lives here
    if xcd.is_file():
        try:
            text = xcd.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        m = (re.search(r'oor:name="ooSetupVersionAboutBox"><value>([0-9][0-9.]*)<', text)
             or re.search(r'oor:name="ooSetupVersion"><value>([0-9][0-9.]*)<', text))
        if m:
            return m.group(1)
    if not run_version or sys.platform == "win32":    # soffice.exe is a GUI binary: --version prints nothing
        return None
    try:
        out = subprocess.run([str(exe), "--version"], check=False, capture_output=True, text=True, timeout=30,
                             errors="replace").stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"(\d+\.\d+(?:\.\d+)*)", out)
    return m.group(1) if m else None


def soffice_info(configured: str = "") -> dict:
    """For GET /system: {found, path, version}."""
    exe = find_soffice(configured)
    if exe is None:
        return {"found": False, "path": None, "version": None, "configured": bool(configured)}
    try:
        stamp = exe.stat().st_mtime
    except OSError:
        stamp = 0.0
    return {"found": True, "path": str(exe), "version": _cached_version(str(exe), stamp),
            "configured": bool(configured)}


@functools.lru_cache(maxsize=8)
def _cached_version(exe: str, _mtime: float) -> str | None:     # GET /system is polled: read main.xcd once
    return soffice_version(Path(exe))


# ---- command line and profile

def build_command(exe: list[str], src: Path, outdir: Path, profile: Path) -> list[str]:
    return [*exe, f"-env:UserInstallation={Path(profile).resolve().as_uri()}", "--headless", "--invisible",
            "--norestore", "--nologo", "--nodefault", "--nolockcheck", "--convert-to", "pdf",
            "--outdir", str(outdir), str(src)]


# Macro security "very high" (only signed macros from trusted locations — there are none in this profile) and
# macro execution disabled outright; linked content (OLE links, external data, images by URL) never updated on load,
# so a document cannot make the server fetch a URL or open an SMB share while it is rendered.
_POLICY = [
    ("/org.openoffice.Office.Common/Security/Scripting", "MacroSecurityLevel", "int", "3"),
    ("/org.openoffice.Office.Common/Security/Scripting", "DisableMacrosExecution", "boolean", "true"),
    ("/org.openoffice.Office.Common/Security/Scripting", "DisableActiveContent", "boolean", "true"),
    ("/org.openoffice.Office.Common/Security/Scripting", "BlockUntrustedRefererLinks", "boolean", "true"),
    ("/org.openoffice.Office.Writer/Content/Update", "Link", "int", "{writer_never}"),
    ("/org.openoffice.Office.Calc/Content/Update", "Link", "int", "{calc_never}"),
    ("/org.openoffice.Office.Common/Misc", "UseLocking", "boolean", "false"),
]
# LibreOffice schema: Writer Content/Update/Link 0 = never, 1 = always, 2 = on request (officecfg Writer.xcs);
# Calc Content/Update/Link 0 = always, 1 = never, 2 = on request (officecfg Calc.xcs).
_LINK_NEVER = {"writer_never": "0", "calc_never": "1"}


def policy_xcu() -> str:
    items = []
    for path, name, typ, value in _POLICY:
        v = value.format(**_LINK_NEVER)
        items.append(f'<item oor:path="{path}"><prop oor:name="{name}" oor:op="fuse" oor:type="xs:{typ}">'
                     f"<value>{v}</value></prop></item>")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<oor:items xmlns:oor="http://openoffice.org/2001/registry" xmlns:xs="http://www.w3.org/2001/XMLSchema"'
            ' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">\n' + "\n".join(items) + "\n</oor:items>\n")


def write_profile_policy(profile: Path) -> None:
    """(Re)write the profile's registrymodifications.xcu with the policy before each run. LibreOffice merges it on
    start; rewriting it every time means a profile it modified can never drift back to running macros."""
    user = Path(profile) / "user"
    user.mkdir(parents=True, exist_ok=True)
    fsops.atomic_write_text(user / "registrymodifications.xcu", policy_xcu())
    lock = user / ".lock"                          # a profile lock left by a killed run: we run one at a time
    try:
        lock.unlink()
    except OSError:
        pass


# ---- cache

class PreviewCache:
    def __init__(self, data_dir: Path):
        self.root = Path(data_dir) / "previews"

    def _check(self, sha: str) -> str:
        if not isinstance(sha, str) or not _SHA.match(sha):
            raise ValueError("not a sha256")
        return sha

    def pdf_path(self, sha: str) -> Path:
        return self.root / f"{self._check(sha)}.pdf"

    def meta_path(self, sha: str) -> Path:
        return self.root / f"{self._check(sha)}.json"

    def read_meta(self, sha: str) -> dict | None:
        try:
            meta = json.loads(self.meta_path(sha).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if meta.get("status") == "ready" and not self.pdf_path(sha).is_file():
            return None
        return meta if isinstance(meta, dict) else None

    def purge(self, known: set[str]) -> int:
        """Remove previews whose source sha256 no longer belongs to any document; returns the number removed."""
        if not self.root.is_dir():
            return 0
        n = 0
        for f in self.root.iterdir():
            if f.is_file() and f.suffix in (".pdf", ".json") and _SHA.match(f.stem) and f.stem not in known:
                try:
                    f.unlink()
                except OSError:
                    continue
                n += f.suffix == ".pdf"
        tmp = self.root / ".tmp"
        if tmp.is_dir():
            for d in tmp.iterdir():
                try:
                    if time.time() - d.stat().st_mtime > 3600:
                        fsops.remove_tree(d)
                except OSError:
                    pass
        return n


# ---- manager

PreviewSettings = Preview          # [preview] in aidoc.toml


def _public(meta: dict) -> dict:
    if meta["status"] == "ready":
        return {"status": "ready", "pages": meta.get("pages")}
    return {"status": "failed", "reason": meta.get("reason", "conversion_failed")}


class PreviewManager:
    """One worker thread; `request()` never blocks on soffice. `publish(payload)` is called when a preview becomes
    ready or fails (the server turns it into the SSE event `document.preview`)."""

    def __init__(self, data_dir: Path, settings: PreviewSettings | Callable[[], PreviewSettings], *,
                 exe: list[str] | None = None, env: dict | None = None,
                 locate: Callable[[], Path | None] | None = None,
                 source_of: Callable[[dict], Path | None] | None = None,
                 publish: Callable[[dict], None] | None = None):
        from aidoc.sources import document_source
        self.cache = PreviewCache(data_dir)
        self._settings = settings if callable(settings) else (lambda: settings)
        self._exe = exe
        self._env = env or {}
        self._locate = locate
        self._source_of = source_of or document_source
        self._publish = publish
        self._lock = threading.Lock()
        self._pending: dict[str, set[str]] = {}          # sha256 -> document ids waiting for it
        self._queue: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._proc: subprocess.Popen | None = None
        self._thread = threading.Thread(target=self._loop, name="aidoc-preview", daemon=True)
        self._thread.start()

    # -- public API

    def executable(self) -> list[str] | None:
        if self._exe is not None:
            return self._exe
        exe = self._locate() if self._locate is not None else find_soffice(self._settings().soffice_path)
        return [str(exe)] if exe is not None else None

    def status(self, doc: dict) -> dict:
        sha = doc["sha256"]
        meta = self.cache.read_meta(sha)
        if meta and meta.get("status") == "ready":
            return _public(meta)
        with self._lock:
            if sha in self._pending:
                return {"status": "pending"}
        blocked = self._precheck(doc, need_source=False)
        if blocked:
            return blocked
        if meta:
            return _public(meta)
        return {"status": "none"}

    def request(self, doc: dict, retry: bool = False) -> dict:
        sha = doc["sha256"]
        meta = self.cache.read_meta(sha)
        if meta and meta.get("status") == "ready":
            return _public(meta)
        with self._lock:
            if sha in self._pending:
                self._pending[sha].add(doc["id"])
                return {"status": "pending"}
        blocked = self._precheck(doc, need_source=True)
        if blocked:
            return blocked
        if meta and not retry:
            return _public(meta)
        with self._lock:
            if sha in self._pending:
                self._pending[sha].add(doc["id"])
            else:
                self._pending[sha] = {doc["id"]}
                self._queue.put(dict(doc))
        return {"status": "pending"}

    def pdf_path(self, doc: dict) -> Path | None:
        meta = self.cache.read_meta(doc["sha256"])
        return self.cache.pdf_path(doc["sha256"]) if meta and meta.get("status") == "ready" else None

    def stop(self) -> None:
        self._stop.set()
        self._queue.put(None)
        proc = self._proc
        if proc is not None and proc.poll() is None:
            kill_tree(proc.pid)
        self._thread.join(10)

    # -- internals

    def _precheck(self, doc: dict, need_source: bool) -> dict | None:
        s = self._settings()
        if not s.enabled:
            return {"status": "failed", "reason": "disabled"}
        if self.executable() is None:
            return {"status": "failed", "reason": "soffice_missing"}
        src = self._source_of(doc)
        if src is None:
            return {"status": "failed", "reason": "source_missing"} if need_source else None
        try:
            size = src.stat().st_size
        except OSError:
            return {"status": "failed", "reason": "source_missing"}
        if size > s.max_mb * 1024 * 1024:
            return {"status": "failed", "reason": "too_large"}
        return None

    def _loop(self) -> None:
        while not self._stop.is_set():
            doc = self._queue.get()
            if doc is None or self._stop.is_set():
                return
            sha = doc["sha256"]
            try:
                result = self._convert(doc)
            except Exception as e:  # noqa: BLE001  the worker must survive anything
                result = {"status": "failed", "reason": "conversion_failed", "detail": f"{type(e).__name__}: {e}"}
            if result.get("reason") not in ("source_missing", "soffice_missing"):   # transient: never cached
                try:
                    fsops.atomic_write_json(self.cache.meta_path(sha), {**result, "sha256": sha, "at": time.time()})
                except OSError:
                    pass
            with self._lock:
                ids = self._pending.pop(sha, set()) | {doc["id"]}
            if self._publish is not None:
                for did in sorted(ids):
                    try:
                        self._publish({"document_id": did, **_public(result)})
                    except Exception as e:  # noqa: BLE001  a failed SSE publish must not stop the worker
                        print(f"preview: publish failed: {e}", file=sys.stderr, flush=True)

    def _convert(self, doc: dict) -> dict:
        s = self._settings()
        exe = self.executable()
        if exe is None:
            return {"status": "failed", "reason": "soffice_missing"}
        src = self._source_of(doc)
        if src is None or not src.is_file():
            return {"status": "failed", "reason": "source_missing"}
        if src.stat().st_size > s.max_mb * 1024 * 1024:
            return {"status": "failed", "reason": "too_large"}
        ext = Path(str(doc.get("source_path") or src.name)).suffix.lower()
        if ext not in OFFICE_EXTS:
            ext = src.suffix.lower()
        self.cache.root.mkdir(parents=True, exist_ok=True)
        work = self.cache.root / ".tmp" / uuid.uuid4().hex
        try:
            work.mkdir(parents=True)
            copy = work / f"src{ext}"
            shutil.copyfile(src, copy)
            if file_sha256(copy) != doc["sha256"]:
                return {"status": "failed", "reason": "source_changed"}
            out = work / "out"
            out.mkdir()
            profile = self.cache.root / ".profile"
            write_profile_policy(profile)
            cmd = build_command(exe, copy, out, profile)
            env = {**os.environ, **self._env}
            t0 = time.time()
            proc = subprocess.Popen(cmd, cwd=str(work), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.PIPE, **popen_kwargs())
            self._proc = proc
            try:
                _, err = proc.communicate(timeout=max(1, int(s.timeout_s)))
            except subprocess.TimeoutExpired:
                kill_tree(proc.pid)
                try:
                    proc.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
                return {"status": "failed", "reason": "timeout"}
            finally:
                self._proc = None
            pdf = out / f"{copy.stem}.pdf"
            if proc.returncode != 0 or not pdf.is_file():
                tail = (err or b"").decode("utf-8", errors="replace").strip()[-300:]
                return {"status": "failed", "reason": "conversion_failed", "detail": tail or f"exit {proc.returncode}"}
            with pdf.open("rb") as fh:
                if not fh.read(1024).lstrip().startswith(b"%PDF-"):
                    return {"status": "failed", "reason": "conversion_failed", "detail": "not a PDF"}
            try:
                import pymupdf as fitz
                with fitz.open(pdf) as d:
                    pages = d.page_count
            except Exception:  # noqa: BLE001
                return {"status": "failed", "reason": "conversion_failed", "detail": "unreadable PDF"}
            fsops.retry_fs(lambda: os.replace(pdf, self.cache.pdf_path(doc["sha256"])))
            return {"status": "ready", "pages": pages, "elapsed_s": round(time.time() - t0, 2)}
        finally:
            try:
                fsops.remove_tree(work)
            except OSError:
                pass

