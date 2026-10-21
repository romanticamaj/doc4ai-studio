"""A recording HTTP proxy for privacy tests: every request that reaches it is logged and answered 502. A loopback
endpoint must never be reached through it (verifier Task 9 finding: urllib / httpx honour HTTP_PROXY even for
127.0.0.1)."""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class RecordingProxy:
    def __init__(self):
        self.hits: list[str] = []
        self.httpd: ThreadingHTTPServer | None = None

    def __enter__(self) -> RecordingProxy:  # noqa: PYI034  (Self needs Python 3.11; the host runs 3.10)
        hits = self.hits

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _any(self):
                hits.append(f"{self.command} {self.path}")
                self.send_response(502)
                self.send_header("Content-Length", "0")
                self.end_headers()
            do_GET = do_POST = do_CONNECT = do_PUT = do_DELETE = _any

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def __exit__(self, *exc) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def point_env_at(monkeypatch, proxy: RecordingProxy) -> None:
    for k in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(k, proxy.url)
    for k in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(k, raising=False)
