"""OAuth client registry (spec 2026-11-05 §5.1 CIMD, §5.2 DCR).

* CIMD: `client_id` is an https URL whose JSON document describes the client (Claude Code). Fetched with an SSRF
  guard (https only, no redirects, every resolved address public, 5 s, 64 KB, JSON only, `client_id` echoed), cached in
  `oauth_clients` (`cache_until = now + clamp(max-age, 60 s, 24 h)`), at most 6 fetches a minute per client id, and
  — when a fetch fails — a document fetched within the last 7 days is used instead (this host may be offline).
* DCR: `POST /oauth/register` creates a public client (`doc4ai_cl_…`, no secret); fixed hourly window per IP.
* `redirect_allowed()`: the one redirect rule for registration and authorization — exact string, except loopback
  `http://localhost|127.0.0.1|[::1]` which ignores the port only (RFC 8252 §7.3)."""
from __future__ import annotations

import ipaddress
import json
import logging
import re
import socket
import ssl
import threading
import time
from collections import deque
from dataclasses import dataclass
from urllib.parse import urlsplit

from aidoc.oauth.tokens import generate_client_id

log = logging.getLogger("aidoc.oauth")

CIMD_MAX_BYTES = 65536
CIMD_TIMEOUT_S = 5                  # total wall-clock budget of one fetch: DNS answer to last byte (spec §5.1)
CIMD_FETCH_PER_MIN = 6
CIMD_MAX_CONCURRENT = 3              # documents fetched at the same time, server-wide; more are refused, never queued
CIMD_NEGATIVE_TTL_S = 30             # a failed fetch (with no usable stale document) is answered from memory this long
CIMD_STALE_OK_S = 7 * 86400          # a failed refetch falls back to a document this recent
CIMD_CACHE_MIN_S = 60
CIMD_CACHE_MAX_S = 86400
CIMD_CACHE_DEFAULT_S = 3600          # no max-age in the response
DCR_UNUSED_DAYS = 30                 # a DCR client that never got a grant is deleted after this
LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1")
URI_MAX = 512
NAME_MAX = 100
DCR_MAX_REDIRECTS = 10
DCR_FIELD_MAX = 512
DCR_METADATA_MAX = 16384             # a registration body (and what is stored of it) is at most this many bytes
DCR_UNUSED_MAX = 50                  # never-granted DCR clients kept server-wide; the oldest unused gives way
DCR_ATTEMPT_FACTOR = 5               # flood ceiling: registrations per source per hour = dcr_per_hour_per_ip × this
DCR_STORED_KEYS = ("client_name", "redirect_uris", "token_endpoint_auth_method", "grant_types", "response_types",
                   "application_type", "scope", "software_id", "software_version", "client_uri", "logo_uri")
BLOCKED_SCHEMES = frozenset({"javascript", "data", "file", "vbscript", "blob", "about", "ftp", "ws", "wss", "mailto", "tel"})
_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.\-]*$")
GRANT_TYPES = ("authorization_code", "refresh_token")
APPLICATION_TYPES = (None, "native", "web")


class ClientError(Exception):
    """`code` is the OAuth error (invalid_redirect_uri, invalid_client_metadata, rate_limited) or an internal reason
    for the authorize error page (unknown_client, invalid_client_id, cimd_disabled, cimd_fetch_failed,
    client_disabled); `message` is shown to the user."""

    def __init__(self, code: str, message: str, retry_after: int | None = None):
        super().__init__(f"{code}: {message}")
        self.code, self.message, self.retry_after = code, message, retry_after


@dataclass
class FetchResult:
    status: int
    headers: dict            # lower-cased names
    body: bytes              # at most max_bytes + 1 bytes (one more than allowed tells "too large")


class BlockedAddress(Exception):
    """The CIMD host resolves to (or the connection reached) an address that is not a public unicast address."""


def resolve_addrs(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(i[4][0] for i in infos))


# IPv6 ranges that embed or translate to an IPv4 address (the embedded address could be anything, private included),
# plus special-use blocks Python does not flag everywhere: all refused outright.
_BLOCKED_NETS = tuple(ipaddress.ip_network(n) for n in (
    "::ffff:0:0/96",        # IPv4-mapped
    "::/96",                # IPv4-compatible (deprecated)
    "64:ff9b::/96",         # NAT64 well-known prefix (RFC 6052)
    "64:ff9b:1::/48",       # NAT64 local-use (RFC 8215)
    "2002::/16",            # 6to4
    "2001::/32",            # Teredo
    "100::/64",             # discard-only
    "2001:db8::/32",        # documentation
    "fc00::/7", "fe80::/10", "fec0::/10", "ff00::/8",
    "100.64.0.0/10",        # CGNAT (Tailscale)
    "192.0.0.0/24", "198.18.0.0/15", "240.0.0.0/4",
))


def _is_public(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr.split("%", 1)[0]) if addr else None
    except ValueError:
        return False
    if ip is None or any(ip.version == n.version and ip in n for n in _BLOCKED_NETS):
        return False
    return ip.is_global and not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast
                                 or ip.is_unspecified)


def check_addresses(addrs: list[str]) -> str:
    """The address to dial (the first): every resolved address must be public, else BlockedAddress."""
    if not addrs:
        raise BlockedAddress("DNS lookup returned no address")
    bad = [a for a in addrs if not _is_public(a)]
    if bad:
        raise BlockedAddress(f"address not allowed ({bad[0]} is not a public address)")
    return addrs[0]


class _DeadlineStream:
    """An httpcore network stream whose every read, write and TLS handshake gets at most the time left until one
    absolute deadline: a peer that trickles a byte at a time cannot stretch the fetch past it (each read alone would
    stay under a per-phase timeout)."""

    def __init__(self, inner, deadline: float):
        self._inner, self._deadline = inner, deadline

    def _left(self, timeout: float | None) -> float:
        import httpcore
        left = self._deadline - time.monotonic()
        if left <= 0:
            raise httpcore.ReadTimeout("CIMD fetch exceeded its total deadline")
        return left if timeout is None else min(timeout, left)

    def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        return self._inner.read(max_bytes, self._left(timeout))

    def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self._inner.write(buffer, self._left(timeout))

    def close(self) -> None:
        self._inner.close()

    def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        return _DeadlineStream(self._inner.start_tls(ssl_context, server_hostname, self._left(timeout)), self._deadline)

    def get_extra_info(self, info: str):
        return self._inner.get_extra_info(info)


def _dial(ip: str, port: int, timeout: float | None):
    """Connect to exactly this address (never a name: no second DNS lookup)."""
    import httpcore
    return httpcore.SyncBackend().connect_tcp(ip, port, timeout=timeout)


class _PinnedBackend:
    """httpcore network backend that ignores the host name httpcore asks for and dials the pre-validated address;
    the TLS layer above still uses the URL's host name for SNI and certificate verification, and the request keeps
    it in the Host header. The peer address of the new connection is checked again."""

    def __init__(self, ip: str, deadline: float):
        self.ip, self.deadline = ip, deadline

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        left = self.deadline - time.monotonic()
        if left <= 0:
            import httpcore
            raise httpcore.ConnectTimeout("CIMD fetch exceeded its total deadline")
        stream = _dial(self.ip, port, left if timeout is None else min(timeout, left))
        sock = stream.get_extra_info("socket")
        peer = sock.getpeername()[0] if sock is not None else None
        if peer is None or ipaddress.ip_address(peer.split("%", 1)[0]) != ipaddress.ip_address(self.ip) \
                or not _is_public(peer):
            stream.close()
            raise BlockedAddress(f"connection peer {peer} is not the validated address")
        return _DeadlineStream(stream, self.deadline)

    def connect_unix_socket(self, *a, **kw):
        raise BlockedAddress("unix sockets are not allowed")

    def sleep(self, seconds: float) -> None:
        import time
        time.sleep(seconds)


def fetch_json(url: str, *, max_bytes: int, timeout: float, ssl_context=None) -> FetchResult:
    """One https GET: the host is resolved once, every address validated, and the connection dials the validated
    address (DNS rebinding cannot move it); no proxies, no redirects (httpcore never follows one; the caller refuses
    3xx); the body is read up to max_bytes + 1. `timeout` is ONE total budget for connect, TLS, request and the whole
    body (verifier F1: per-phase timeouts let a trickling server hold the fetch for hours)."""
    import httpcore
    deadline = time.monotonic() + timeout
    u = urlsplit(url)
    if u.scheme != "https" or not u.hostname:
        raise BlockedAddress("only https URLs are fetched")
    ip = check_addresses(resolve_addrs(u.hostname))
    ctx = ssl_context or ssl.create_default_context()
    t = {"connect": timeout, "read": timeout, "write": timeout, "pool": timeout}
    with httpcore.ConnectionPool(ssl_context=ctx, network_backend=_PinnedBackend(ip, deadline), max_connections=1,
                                 retries=0) as pool, \
            pool.stream("GET", url, headers={"Accept": "application/json", "User-Agent": "Doc4AI-Studio-CIMD"},
                        extensions={"timeout": t}) as r:
        body = bytearray()
        for chunk in r.iter_stream():
            body += chunk
            if len(body) > max_bytes:
                break
            if time.monotonic() >= deadline:
                raise httpcore.ReadTimeout("CIMD fetch exceeded its total deadline")
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in r.headers}
        return FetchResult(status=r.status, headers=headers, body=bytes(body[: max_bytes + 1]))


def classify_redirect(uri) -> str | None:
    """"https" | "loopback" | "private" (RFC 8252 §7.1 private-use scheme) | None (never acceptable)."""
    if not isinstance(uri, str) or not uri or len(uri) > URI_MAX or "#" in uri:
        return None
    try:
        u = urlsplit(uri)
    except ValueError:
        return None
    scheme = u.scheme.lower()
    if not scheme or not _SCHEME_RE.match(scheme):
        return None
    if scheme in ("http", "https"):
        try:
            host, _port = u.hostname, u.port
        except ValueError:
            return None
        if not host or "@" in u.netloc:
            return None
        if scheme == "https":
            return "https"
        return "loopback" if host.lower() in LOOPBACK_HOSTS else None
    if scheme in BLOCKED_SCHEMES:
        return None
    return "private"


def _loopback_key(uri: str) -> tuple:
    u = urlsplit(uri)
    return (u.scheme.lower(), (u.hostname or "").lower(), u.path, u.query)


def redirect_allowed(client_row: dict, uri: str) -> bool:
    kind = classify_redirect(uri)
    if kind is None:
        return False
    for reg in client_row.get("redirect_uris") or []:
        if kind == "loopback":
            if classify_redirect(reg) == "loopback" and _loopback_key(reg) == _loopback_key(uri):
                return True
        elif reg == uri and classify_redirect(reg) == kind:
            return True
    return False


def _max_age(cache_control: str | None) -> int:
    m = re.search(r"max-age\s*=\s*(\d+)", cache_control or "", re.IGNORECASE)
    if not m:
        return CIMD_CACHE_DEFAULT_S
    return max(CIMD_CACHE_MIN_S, min(CIMD_CACHE_MAX_S, int(m.group(1))))


def cimd_id_problem(client_id: str) -> str | None:
    if len(client_id) > URI_MAX:
        return "client_id 太長"
    try:
        u = urlsplit(client_id)
    except ValueError:
        return "client_id 不是 URL"
    if u.scheme != "https" or not u.hostname or "@" in u.netloc or u.query or u.fragment or "#" in client_id \
            or u.path in ("", "/"):
        return "client_id 必須是 https URL（含路徑，不含帳密、query、fragment）"
    return None


_FETCH_SLOTS = threading.BoundedSemaphore(CIMD_MAX_CONCURRENT)


class ClientRegistry:
    def __init__(self, ctx):
        self.ctx = ctx
        self._fetches: dict[str, deque] = {}
        self._failed: dict[str, tuple[float, str]] = {}      # client_id -> (until, reason): the negative cache
        self._dcr: dict[str, tuple[float, int]] = {}         # ip -> (window start, attempts): the flood ceiling
        self._dcr_recent: dict[str, deque] = {}               # ip -> (ts, client_id) registered in the last hour
        self._lock = threading.Lock()

    @property
    def store(self):
        return self.ctx.store

    # ---- resolution (authorize, token)
    def resolve(self, client_id: str, now: float) -> dict:
        if not isinstance(client_id, str) or not client_id:
            raise ClientError("unknown_client", "找不到這個 client")
        if client_id.lower().startswith(("https:", "http:")):
            return self._resolve_cimd(client_id, now)
        row = self.store.get_oauth_client(client_id)
        if row is None or row.get("kind") != "dcr":
            raise ClientError("unknown_client", "找不到這個 client")
        if row.get("disabled_at") is not None:
            raise ClientError("client_disabled", "這個 client 已停用")
        return row

    def _resolve_cimd(self, client_id: str, now: float) -> dict:
        if not self.ctx.config.oauth.allow_cimd:
            raise ClientError("cimd_disabled", "這台伺服器未允許以 URL 識別的 client（oauth.allow_cimd）")
        problem = cimd_id_problem(client_id)
        if problem:
            raise ClientError("invalid_client_id", problem)
        row = self.store.get_oauth_client(client_id)
        if row is not None and row.get("kind") == "cimd":
            if row.get("disabled_at") is not None:
                raise ClientError("client_disabled", "這個 client 已停用")
            if (row.get("cache_until") or 0) > now:
                return row
        try:
            with self._lock:
                failed = self._failed.get(client_id)
            if failed is not None and failed[0] > now:
                cached = ClientError("cimd_fetch_failed", failed[1])
                cached.cached = True                      # answered from memory: does not extend the window
                raise cached
            if not self._may_fetch(client_id, now):
                raise ClientError("cimd_fetch_failed", "too many fetches of this document; try again in a minute")
            doc, max_age = self._fetch(client_id)
        except ClientError as e:
            stale = row is not None and row.get("kind") == "cimd" and row.get("fetched_at") is not None \
                and now - row["fetched_at"] <= CIMD_STALE_OK_S
            if stale:
                log.warning("CIMD fetch of %s failed (%s); using the document fetched %.0f s ago", client_id, e.message,
                            now - row["fetched_at"])
                return row
            if not e.message.startswith("busy") and not getattr(e, "cached", False):   # a full cap is not its fault
                with self._lock:
                    self._failed[client_id] = (now + CIMD_NEGATIVE_TTL_S, e.message)
                    if len(self._failed) > 1000:
                        for k in [k for k, (until, _m) in self._failed.items() if until <= now]:
                            del self._failed[k]
            raise
        with self._lock:
            self._failed.pop(client_id, None)
        self.store.upsert_oauth_client(
            client_id=client_id, kind="cimd", name=doc.get("client_name") or urlsplit(client_id).hostname,
            redirect_uris=doc["redirect_uris"], metadata_url=client_id, client_uri=_opt_str(doc.get("client_uri")),
            logo_uri=_opt_str(doc.get("logo_uri")), software_id=_opt_str(doc.get("software_id")),
            software_version=_opt_str(doc.get("software_version")), application_type=doc.get("application_type"),
            metadata_json=doc, fetched_at=now, cache_until=now + max_age)
        return self.store.get_oauth_client(client_id)

    def _may_fetch(self, client_id: str, now: float) -> bool:
        with self._lock:
            q = self._fetches.setdefault(client_id, deque())
            while q and now - q[0] >= 60:
                q.popleft()
            if len(q) >= CIMD_FETCH_PER_MIN:
                return False
            q.append(now)
            if len(self._fetches) > 1000:
                for k in [k for k, v in self._fetches.items() if not v or now - v[-1] >= 60]:
                    del self._fetches[k]
            return True

    def _fetch(self, url: str) -> tuple[dict, int]:
        def fail(reason):
            return ClientError("cimd_fetch_failed", reason)
        if not _FETCH_SLOTS.acquire(blocking=False):
            raise fail("busy: other client documents are being fetched; try again in a few seconds")
        try:
            res = fetch_json(url, max_bytes=CIMD_MAX_BYTES, timeout=CIMD_TIMEOUT_S)
        except BlockedAddress as e:
            raise fail(str(e)) from None
        except OSError as e:
            raise fail(f"DNS lookup or connection failed ({type(e).__name__})") from None
        except Exception as e:  # noqa: BLE001  any transport / TLS error is one reason: unreachable
            raise fail(f"unreachable ({type(e).__name__})") from None
        finally:
            _FETCH_SLOTS.release()
        if 300 <= res.status < 400:
            raise fail(f"HTTP {res.status}: redirect not allowed")
        if res.status != 200:
            raise fail(f"HTTP {res.status}")
        if "json" not in (res.headers.get("content-type") or "").lower():
            raise fail("content type is not json")
        if len(res.body) > CIMD_MAX_BYTES:
            raise fail("document larger than 64 KB")
        try:
            doc = json.loads(res.body)
        except ValueError:
            raise fail("document is not valid json") from None
        if not isinstance(doc, dict):
            raise fail("document is not a json object")
        if doc.get("client_id") != url:
            raise fail("client_id in the document does not equal its URL")
        uris = doc.get("redirect_uris")
        if not isinstance(uris, list) or not uris or not all(isinstance(u, str) and u and len(u) <= URI_MAX for u in uris):
            raise fail("redirect_uris must be a non-empty list of strings")
        if doc.get("token_endpoint_auth_method") not in (None, "none"):
            raise fail("token_endpoint_auth_method must be none")
        name = doc.get("client_name")
        if name is not None and (not isinstance(name, str) or len(name) > NAME_MAX):
            raise fail("client_name must be a string of at most 100 characters")
        if doc.get("application_type") not in APPLICATION_TYPES:
            raise fail("application_type must be native or web")
        return doc, _max_age(res.headers.get("cache-control"))

    # ---- DCR (RFC 7591 subset)
    def _count_dcr(self, ip: str | None, now: float) -> None:
        """Flood ceiling only: a source may attempt dcr_per_hour_per_ip × DCR_ATTEMPT_FACTOR registrations an hour."""
        limit = self.ctx.config.oauth.dcr_per_hour_per_ip * DCR_ATTEMPT_FACTOR
        key = ip or "?"
        with self._lock:
            start, n = self._dcr.get(key, (now, 0))
            if now - start >= 3600:
                start, n = now, 0
            if n >= limit:
                self._dcr[key] = (start, n)
                raise ClientError("rate_limited", "too many client registrations from this address",
                                  retry_after=max(1, int(start + 3600 - now)))
            self._dcr[key] = (start, n + 1)
            if len(self._dcr) > 10000:
                for k in [k for k, (s, _n) in self._dcr.items() if now - s >= 3600]:
                    del self._dcr[k]

    def _make_room(self, ip: str | None, now: float) -> None:
        """Verifier F2: junk registrations must not lock a real client out. A source keeps at most dcr_per_hour_per_ip
        unused (never-granted) registrations from the last hour, the server at most DCR_UNUSED_MAX: past either, the
        oldest unused registration is deleted to make room. A client that got a grant is never touched."""
        limit = self.ctx.config.oauth.dcr_per_hour_per_ip
        key = ip or "?"
        with self._lock:
            q = self._dcr_recent.setdefault(key, deque())
            while q and now - q[0][0] >= 3600:
                q.popleft()
            live = deque((t, cid) for t, cid in q if self.store.is_unused_dcr_client(cid))
            while len(live) >= limit:
                _t, cid = live.popleft()
                if self.store.delete_unused_dcr_client(cid):
                    log.info("DCR: %s unused registrations from %s; removed the oldest (%s)", limit, key, cid[:14])
            self._dcr_recent[key] = live
        unused = self.store.unused_dcr_clients()                         # oldest first; leave room for the new one
        for cid in unused[: max(0, len(unused) - (DCR_UNUSED_MAX - 1))]:
            self.store.delete_unused_dcr_client(cid)

    def register_dcr(self, body, ip: str | None, now: float) -> dict:
        """Every attempt counts toward the flood ceiling (also malformed ones); a valid registration first makes room
        among unused ones (see _make_room). Returns the RFC 7591 response."""
        self._count_dcr(ip, now)

        def meta(msg):
            return ClientError("invalid_client_metadata", msg)
        if not isinstance(body, dict):
            raise meta("the body must be a JSON object")
        if len(json.dumps(body, ensure_ascii=False).encode("utf-8")) > DCR_METADATA_MAX:
            raise meta(f"the registration is larger than {DCR_METADATA_MAX} bytes")
        uris = body.get("redirect_uris")
        if not isinstance(uris, list) or not 1 <= len(uris) <= DCR_MAX_REDIRECTS:
            raise ClientError("invalid_redirect_uri", f"redirect_uris must list 1 to {DCR_MAX_REDIRECTS} URIs")
        for u in uris:
            if classify_redirect(u) is None:
                raise ClientError("invalid_redirect_uri", "redirect URIs must be https, http on localhost / 127.0.0.1 / [::1], "
                                                          "or a private-use scheme (no fragment)")
        name = body.get("client_name")
        if name is not None and (not isinstance(name, str) or not name.strip() or len(name) > NAME_MAX):
            raise meta("client_name must be a string of at most 100 characters")
        if body.get("token_endpoint_auth_method") not in (None, "none"):
            raise meta("only public clients are supported: token_endpoint_auth_method must be none")
        grants = body.get("grant_types", list(GRANT_TYPES))
        if not isinstance(grants, list) or not grants or not set(grants) <= set(GRANT_TYPES):
            raise meta("grant_types must be a subset of authorization_code, refresh_token")
        rtypes = body.get("response_types", ["code"])
        if not isinstance(rtypes, list) or not rtypes or not set(rtypes) <= {"code"}:
            raise meta("response_types must be [\"code\"]")
        if body.get("application_type") not in APPLICATION_TYPES:
            raise meta("application_type must be native or web")
        opt = {}
        for key in ("scope", "software_id", "software_version", "client_uri", "logo_uri"):
            v = body.get(key)
            if v is None:
                continue
            if not isinstance(v, str) or len(v) > DCR_FIELD_MAX:
                raise meta(f"{key} must be a string of at most {DCR_FIELD_MAX} characters")
            opt[key] = v
        cid = generate_client_id()
        name = name.strip() if isinstance(name, str) else None
        self._make_room(ip, now)
        stored = {k: body[k] for k in DCR_STORED_KEYS if k in body}           # only what RFC 7591 defines and we use
        self.store.upsert_oauth_client(
            client_id=cid, kind="dcr", name=name or "未命名 client", redirect_uris=uris, client_uri=opt.get("client_uri"),
            logo_uri=opt.get("logo_uri"), software_id=opt.get("software_id"), software_version=opt.get("software_version"),
            application_type=body.get("application_type"), metadata_json=stored)
        with self._lock:
            self._dcr_recent.setdefault(ip or "?", deque()).append((now, cid))
        out = {"client_id": cid, "client_id_issued_at": int(now), "token_endpoint_auth_method": "none",
               "redirect_uris": uris, "grant_types": grants, "response_types": rtypes, **opt}
        if name:
            out["client_name"] = name
        if body.get("application_type"):
            out["application_type"] = body["application_type"]
        return out


def _opt_str(v):
    return v if isinstance(v, str) and len(v) <= DCR_FIELD_MAX else None
