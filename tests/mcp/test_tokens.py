import os
import stat
import sys

import pytest

from aidoc.mcp import tokens as T


def test_generate_token_shape_and_checksum():
    t = T.generate_token()
    assert t.startswith(T.PREFIX) and len(t) == T.TOKEN_LEN == 61
    body, crc = t[len(T.PREFIX):].split("_")
    assert len(body) == 43 and len(crc) == 6
    assert set(body) <= set(T.ALPHABET) and set(crc) <= set(T.ALPHABET)
    assert T.crc_b62(body) == crc
    assert T.is_well_formed(t)
    assert T.display_prefix(t) == T.PREFIX + body[:4]


def test_tokens_are_unique_and_high_entropy():
    seen = {T.generate_token() for _ in range(500)}
    assert len(seen) == 500


@pytest.mark.parametrize("mutate", [
    lambda t: t[:-1],                      # truncated
    lambda t: t[:-1] + ("A" if t[-1] != "A" else "B"),   # bad crc
    lambda t: t.replace(T.PREFIX, "doc4ai_pxt_"),
    lambda t: t[: len(T.PREFIX) + 10] + "!" + t[len(T.PREFIX) + 11:],   # illegal char
    lambda t: t.replace("_", "-", 2),
    lambda t: "",
    lambda t: "Bearer " + t,
])
def test_malformed_tokens_are_rejected(mutate):
    assert not T.is_well_formed(mutate(T.generate_token()))


def test_b62encode_width_and_roundtrip_range():
    assert T.b62encode(0, 6) == "000000"
    assert T.b62encode(61, 2) == "0z"
    assert T.b62encode(62, 2) == "10"
    assert len(T.b62encode(2 ** 256 - 1, 43)) == 43      # every 256-bit value fits in 43 chars


def test_prefix_seen_never_returns_the_secret_part():
    t = T.generate_token()
    assert T.prefix_seen(t) == t[:15] and len(T.prefix_seen(t)) == 15
    assert T.prefix_seen("garbage") is None and T.prefix_seen(None) is None


def test_hash_is_keyed_and_stable():
    t = T.generate_token()
    h1 = T.token_hash(b"k" * 32, t)
    assert h1 == T.token_hash(b"k" * 32, t) and len(h1) == 64
    assert h1 != T.token_hash(b"j" * 32, t)
    assert h1 != T.token_hash(b"k" * 32, t[:-1] + "0")


def test_secret_created_once_and_private(tmp_path):
    s1 = T.load_or_create_secret(tmp_path)
    s2 = T.load_or_create_secret(tmp_path)
    assert s1 == s2 and len(s1) == 32
    p = tmp_path / "secret.key"
    assert p.read_bytes() == s1
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
