"""CIMD fetch resource limits (verifier F1, spec 2026-11-05 §5.1): one total wall-clock deadline for the whole fetch, a
small global cap on fetches in flight (refuse, never queue), failures cached briefly, and resolution kept off the shared
request threadpool so slow client documents cannot starve the rest of the server."""
import socket
import ssl
import threading
import time

import httpx
import pytest

from doc4ai.oauth import clients
from doc4ai.oauth.clients import ClientError, ClientRegistry


@pytest.fixture
def trickle_server(tmp_path):
    """TLS server for cimd.test that sends headers, then one byte every 0.9 s (each read well under any per-read timeout)."""
    from tests.oauth.test_cimd_ssrf import _cert
    cp, kp = _cert(tmp_path, "cimd.test")
    sctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    sctx.load_cert_chain(cp, kp)
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(2)

    def run():
        try:
            raw, _ = srv.accept()
            with sctx.wrap_socket(raw, server_side=True) as c:
                d = b""
                while b"\r\n\r\n" not in d:
                    d += c.recv(4096)
                c.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 20\r\n\r\n")
                for _ in range(20):
                    time.sleep(0.9)
                    c.sendall(b" ")
        except OSError:
            pass
    threading.Thread(target=run, daemon=True).start()
    yield srv.getsockname()[1], ssl.create_default_context(cafile=str(cp))
    srv.close()


def test_trickle_fetch_stops_at_the_total_deadline(monkeypatch, trickle_server):
    port, cctx = trickle_server
    monkeypatch.setattr(clients, "resolve_addrs", lambda h: ["127.0.0.1"])
    monkeypatch.setattr(clients, "_is_public", lambda a: a == "127.0.0.1")
    t0 = time.monotonic()
    with pytest.raises(Exception) as ei:
        clients.fetch_json(f"https://cimd.test:{port}/m", max_bytes=1000, timeout=2, ssl_context=cctx)
    elapsed = time.monotonic() - t0
    assert elapsed < 2.6, elapsed                                       # deadline + epsilon, not 18 s of trickle
    assert "timeout" in type(ei.value).__name__.lower() or "deadline" in str(ei.value).lower()


def test_registry_maps_the_deadline_to_a_fetch_failure(monkeypatch, trickle_server, oauth_ctx):
    port, cctx = trickle_server
    real = clients.fetch_json
    monkeypatch.setattr(clients, "resolve_addrs", lambda h: ["127.0.0.1"])
    monkeypatch.setattr(clients, "_is_public", lambda a: a == "127.0.0.1")
    monkeypatch.setattr(clients, "CIMD_TIMEOUT_S", 1.5)
    monkeypatch.setattr(clients, "fetch_json", lambda url, **kw: real(url, **{**kw, "ssl_context": cctx}))
    t0 = time.monotonic()
    with pytest.raises(ClientError, match="cimd_fetch_failed"):
        ClientRegistry(oauth_ctx).resolve(f"https://cimd.test:{port}/m", now=time.time())
    assert time.monotonic() - t0 < 2.1


def test_failures_are_cached_briefly(fake_cimd, oauth_ctx):
    reg = ClientRegistry(oauth_ctx)
    url = "https://down.test/m"
    fake_cimd.put(url, None, status=503)
    t0 = 1_900_000_000.0
    for dt in (0, 1, 10, 29):
        with pytest.raises(ClientError, match="503"):
            reg.resolve(url, now=t0 + dt)
    assert fake_cimd.calls_for(url) == 1                                # one fetch per negative-cache window
    with pytest.raises(ClientError):
        reg.resolve(url, now=t0 + clients.CIMD_NEGATIVE_TTL_S + 1)
    assert fake_cimd.calls_for(url) == 2


def test_global_fetch_cap_refuses_without_queueing(monkeypatch, oauth_ctx):
    gate, started = threading.Event(), []

    def slow_fetch(url, **kw):
        started.append(url)
        gate.wait(5)
        raise OSError("slow host")
    monkeypatch.setattr(clients, "fetch_json", slow_fetch)
    reg = ClientRegistry(oauth_ctx)
    errors: list[str] = []

    def one(i):
        try:
            reg.resolve(f"https://slow{i}.test/m", now=time.time())
        except ClientError as e:
            errors.append(e.message)
    threads = [threading.Thread(target=one, args=(i,)) for i in range(clients.CIMD_MAX_CONCURRENT + 5)]
    for t in threads:
        t.start()
    deadline = time.monotonic() + 3
    while len(errors) < 5 and time.monotonic() < deadline:
        time.sleep(0.02)
    busy = [e for e in errors if "busy" in e]
    assert len(started) == clients.CIMD_MAX_CONCURRENT and len(busy) == 5   # refused at once, never queued
    gate.set()
    for t in threads:
        t.join(5)


def test_slow_client_documents_do_not_starve_the_server(monkeypatch, live_oauth):
    """More concurrent authorize requests for slow CIMD hosts than the shared threadpool has threads: a regular /api
    request still answers at once, and only CIMD_MAX_CONCURRENT documents are ever fetched at the same time."""
    lock, active, peak = threading.Lock(), [0], [0]

    def slow_fetch(url, **kw):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        try:
            time.sleep(3)
            raise OSError("slow host")
        finally:
            with lock:
                active[0] -= 1
    monkeypatch.setattr(clients, "fetch_json", slow_fetch)
    n = 48                                                               # > the 40 threads of the default anyio pool
    results: list[int] = []

    def hit(i):
        try:
            r = httpx.get(f"{live_oauth}/oauth/authorize", params={"client_id": f"https://slow{i}.test/m", "redirect_uri": "x",
                                                                  "state": "s"}, timeout=30)
            results.append(r.status_code)
        except httpx.HTTPError:
            results.append(-1)
    threads = [threading.Thread(target=hit, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    time.sleep(0.5)
    t0 = time.monotonic()
    r = httpx.get(f"{live_oauth}/api/settings", timeout=30)
    took = time.monotonic() - t0
    for t in threads:
        t.join(30)
    assert r.status_code == 200 and took < 1.5, took
    assert peak[0] <= clients.CIMD_MAX_CONCURRENT
    assert len(results) == n and set(results) == {400}                   # every authorize answered with the error page
