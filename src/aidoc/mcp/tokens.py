"""Personal access token format (spec MCP §2.3): `doc4ai_pat_<43 base62>_<6 base62 CRC32>`.

The prefix lets secret scanners recognise a leaked token; the checksum lets the server drop malformed strings
before any database lookup. Only HMAC-SHA256(server_secret, token) is ever stored."""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import zlib
from pathlib import Path

PREFIX = "doc4ai_pat_"
BODY_LEN = 43                       # 62**43 > 2**256: 32 random bytes always fit
CRC_LEN = 6                         # 62**6 > 2**32
TOKEN_LEN = len(PREFIX) + BODY_LEN + 1 + CRC_LEN      # 61
ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_ALPHA = set(ALPHABET)
SECRET_FILE = "secret.key"


def b62encode(n: int, width: int) -> str:
    out = []
    while n:
        n, r = divmod(n, 62)
        out.append(ALPHABET[r])
    s = "".join(reversed(out)) or "0"
    return s.rjust(width, "0")


def crc_b62(body: str) -> str:
    return b62encode(zlib.crc32(body.encode("ascii")) & 0xFFFFFFFF, CRC_LEN)


def generate_token() -> str:
    body = b62encode(int.from_bytes(secrets.token_bytes(32), "big"), BODY_LEN)
    return f"{PREFIX}{body}_{crc_b62(body)}"


def is_well_formed(token: str) -> bool:
    if not isinstance(token, str) or len(token) != TOKEN_LEN or not token.startswith(PREFIX):
        return False
    rest = token[len(PREFIX):]
    if rest[BODY_LEN] != "_":
        return False
    body, crc = rest[:BODY_LEN], rest[BODY_LEN + 1:]
    if not (set(body) <= _ALPHA and set(crc) <= _ALPHA):
        return False
    return hmac.compare_digest(crc_b62(body), crc)


def display_prefix(token: str) -> str:
    return PREFIX + token[len(PREFIX):len(PREFIX) + 4]


def prefix_seen(raw: str | None) -> str | None:
    """What a failed attempt gets logged as: the prefix plus 4 chars, never more (spec §3 `token_prefix_seen`)."""
    if isinstance(raw, str) and raw.startswith(PREFIX):
        return raw[: len(PREFIX) + 4]
    return None


def token_hash(secret: bytes, token: str) -> str:
    return hmac.new(secret, token.encode("utf-8"), hashlib.sha256).hexdigest()


class SecretKeyError(RuntimeError):
    """`data/secret.key` exists but is not a 32-byte key. Never repaired automatically: a new key would silently
    invalidate every issued token (only their HMACs are stored)."""


SECRET_LEN = 32


def _damaged(p: Path, n: int) -> SecretKeyError:
    return SecretKeyError(
        f"{p} has {n} bytes; a Doc4AI Studio secret key is exactly {SECRET_LEN} bytes. Refusing to start: replacing it "
        f"would silently invalidate every MCP token. To recover, restore the original file from a backup. To start over "
        f"instead, delete (or move away) {p}; a new key is created on the next start and every existing MCP token stops "
        f"working - re-issue them on the MCP page (Tokens) and update your clients.")


def _read_secret(p: Path) -> bytes:
    data = p.read_bytes()
    if len(data) != SECRET_LEN:
        raise _damaged(p, len(data))
    return data


def load_or_create_secret(data_dir: Path) -> bytes:
    """`data/secret.key`: 32 random bytes created on first use; readable only by the owner where the OS allows.
    An existing file is never replaced: a wrong length raises `SecretKeyError` with recovery steps, and a key that
    another process created meanwhile wins over ours (link/rename refuse to overwrite)."""
    p = Path(data_dir) / SECRET_FILE
    if p.exists():
        return _read_secret(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = secrets.token_bytes(SECRET_LEN)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    try:
        try:
            os.link(tmp, p)                 # atomic and never overwrites (FileExistsError)
        except FileExistsError:
            return _read_secret(p)
        except OSError:                     # no hard links here: rename, which also refuses on Windows
            if p.exists():
                return _read_secret(p)
            os.rename(tmp, p)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return data
