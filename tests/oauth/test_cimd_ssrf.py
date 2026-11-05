"""CIMD fetch network layer (spec 2026-11-05 §5.1): DNS is resolved once, every address is validated, and the connection
dials exactly the validated address (no second lookup: DNS rebinding cannot redirect it), with the original host name
kept for SNI, certificate verification and the Host header. Redirects are never followed."""
import datetime
import ipaddress
import socket
import ssl
import threading

import pytest

from aidoc.oauth import clients


class Dialed(Exception):
    pass


def _resolver(seq):
    calls = []

    def resolve(host):
        calls.append(host)
        return list(seq[min(len(calls), len(seq)) - 1])
    return resolve, calls


@pytest.fixture
def local_listener():
    """A TCP listener on 127.0.0.1 that counts connections (the rebinding target)."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    hits = []

    def run():
        while True:
            try:
                c, addr = srv.accept()
            except OSError:
                return
            hits.append(addr)
            c.close()
    th = threading.Thread(target=run, daemon=True)
    th.start()
    yield srv.getsockname()[1], hits
    srv.close()


def test_rebinding_second_lookup_is_never_dialed(monkeypatch, local_listener):
    port, hits = local_listener
    resolve, calls = _resolver([["93.184.216.34"], ["127.0.0.1"]])        # public first, loopback on any later lookup
    dialed = []

    def dial(ip, port_, timeout):
        dialed.append(ip)
        raise Dialed(ip)
    monkeypatch.setattr(clients, "resolve_addrs", resolve)
    monkeypatch.setattr(clients, "_dial", dial)
    with pytest.raises(Dialed):
        clients.fetch_json(f"https://rebind.test:{port}/m", max_bytes=1000, timeout=2)
    assert calls == ["rebind.test"] and dialed == ["93.184.216.34"] and hits == []


def test_blocked_address_never_dialed(monkeypatch):
    dialed = []
    monkeypatch.setattr(clients, "_dial", lambda ip, port, timeout: dialed.append(ip))
    monkeypatch.setattr(clients, "resolve_addrs", lambda host: ["93.184.216.34", "10.0.0.5"])
    with pytest.raises(clients.BlockedAddress, match="address"):
        clients.fetch_json("https://mixed.test/m", max_bytes=1000, timeout=2)
    assert dialed == []
    monkeypatch.setattr(clients, "resolve_addrs", lambda host: [])
    with pytest.raises(clients.BlockedAddress):
        clients.fetch_json("https://empty.test/m", max_bytes=1000, timeout=2)
    with pytest.raises(clients.BlockedAddress):
        clients.fetch_json("http://plain.test/m", max_bytes=1000, timeout=2)           # https only


def test_peer_address_is_rechecked(monkeypatch, local_listener):
    """Whatever the dialer did, a connection whose peer is not the validated public address is dropped."""
    port, _hits = local_listener
    monkeypatch.setattr(clients, "resolve_addrs", lambda host: ["93.184.216.34"])
    real = clients._dial
    monkeypatch.setattr(clients, "_dial", lambda ip, p, timeout: real("127.0.0.1", port, timeout))
    with pytest.raises(clients.BlockedAddress, match="peer"):
        clients.fetch_json(f"https://peer.test:{port}/m", max_bytes=1000, timeout=2)


@pytest.mark.parametrize("addr", [
    "127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.0.5", "169.254.1.1", "100.64.3.3", "0.0.0.0", "224.0.0.1", "240.0.0.1",
    "255.255.255.255", "192.0.0.1", "198.18.0.1", "::1", "::", "fd00::1", "fe80::1", "ff02::1",
    "::ffff:127.0.0.1", "::ffff:93.184.216.34",          # IPv4-mapped: refused outright, whatever it embeds
    "::7f00:1", "::5db8:d822",                           # IPv4-compatible ::/96
    "64:ff9b::7f00:1", "64:ff9b::5db8:d822",             # NAT64 well-known prefix
    "64:ff9b:1::5db8:d822",                              # NAT64 local-use
    "2002:7f00:1::1", "2002:5db8:d822::1",               # 6to4
    "2001:0:4136:e378:8000:63bf:3fff:fdd2",              # Teredo
    "100::1", "2001:db8::1",                             # discard-only, documentation
    "not-an-ip", "",
])
def test_embedded_and_special_addresses_blocked(addr):
    assert clients._is_public(addr) is False


@pytest.mark.parametrize("addr", ["93.184.216.34", "104.18.1.1", "2606:4700::6810:101", "2a00:1450:4001:80b::200e"])
def test_public_addresses_allowed(addr):
    assert clients._is_public(addr) is True


def test_redirects_are_refused_by_the_registry(fake_cimd, oauth_ctx):
    """3xx is never followed (the next hop would be an unvalidated host); a redirect to a private host included."""
    fake_cimd.put("https://hop.test/m", None, status=307, headers={"Location": "https://127.0.0.1/m"})
    with pytest.raises(clients.ClientError, match="redirect"):
        clients.ClientRegistry(oauth_ctx).resolve("https://hop.test/m", now=1.0)
    assert fake_cimd.calls_for("https://127.0.0.1/m") == 0


# ---- the real transport end to end: TLS to a local server, certificate checked against the original host name

def _cert(tmp_path, host):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]), critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    cp, kp = tmp_path / "c.pem", tmp_path / "k.pem"
    cp.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    kp.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return cp, kp


@pytest.fixture
def tls_server(tmp_path):
    cp, kp = _cert(tmp_path, "cimd.test")
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cp, kp)
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)
    seen = {}

    def run():
        while True:
            try:
                raw, _ = srv.accept()
            except OSError:
                return
            try:
                with ctx.wrap_socket(raw, server_side=True) as c:
                    seen["sni"] = c.server_hostname if hasattr(c, "server_hostname") else None
                    data = b""
                    while b"\r\n\r\n" not in data:
                        chunk = c.recv(4096)
                        if not chunk:
                            break
                        data += chunk
                    seen["request"] = data.decode("latin-1")
                    body = seen.get("body", b'{"ok": true}')
                    c.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                              + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body)
            except (OSError, ssl.SSLError) as e:
                seen["error"] = repr(e)
    th = threading.Thread(target=run, daemon=True)
    th.start()
    client_ctx = ssl.create_default_context(cafile=str(cp))
    yield srv.getsockname()[1], seen, client_ctx
    srv.close()


def test_real_transport_pins_ip_and_keeps_host_for_tls(monkeypatch, tls_server):
    port, seen, client_ctx = tls_server
    monkeypatch.setattr(clients, "resolve_addrs", lambda host: ["127.0.0.1"])
    monkeypatch.setattr(clients, "_is_public", lambda a: a == "127.0.0.1")     # this test's server is local
    res = clients.fetch_json(f"https://cimd.test:{port}/m", max_bytes=1000, timeout=5, ssl_context=client_ctx)
    assert res.status == 200 and res.body == b'{"ok": true}' and "json" in res.headers["content-type"]
    assert f"host: cimd.test:{port}" in seen["request"].lower() and "GET /m HTTP/1.1" in seen["request"]
    seen["body"] = b"x" * 5000
    res = clients.fetch_json(f"https://cimd.test:{port}/m", max_bytes=1000, timeout=5, ssl_context=client_ctx)
    assert len(res.body) == 1001                                              # capped at max_bytes + 1


def test_real_transport_verifies_the_certificate_name(monkeypatch, tls_server):
    port, _seen, client_ctx = tls_server
    monkeypatch.setattr(clients, "resolve_addrs", lambda host: ["127.0.0.1"])
    monkeypatch.setattr(clients, "_is_public", lambda a: a == "127.0.0.1")
    with pytest.raises(Exception) as ei:                                      # the cert is for cimd.test, not other.test
        clients.fetch_json(f"https://other.test:{port}/m", max_bytes=1000, timeout=5, ssl_context=client_ctx)
    assert "certificate" in repr(ei.value).lower() or "hostname" in repr(ei.value).lower()


def test_ipaddress_sanity():
    assert ipaddress.ip_address("64:ff9b::7f00:1") in ipaddress.ip_network("64:ff9b::/96")
