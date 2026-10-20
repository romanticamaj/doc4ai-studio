"""Verifier round 3 (finding C again): adjacent whitespace quantifiers in a custom page marker backtracked for > 40 s
on 3,000 spaces. Adjacent whitespace tokens merge into one run; a fuzz test throws random templates at adversarial
inputs with a per-call time bound."""
import random
import time

import pytest

from aidoc.markers import compile_marker, to_aidoc

PER_CALL_S = 0.5
SPACES = "x" + " " * 3000 + "!\n"


@pytest.mark.timeout(60)
@pytest.mark.parametrize("tmpl", [r"x\s*\s*\s*\s*(\d+)y", r"x\s*\s+\s*\s+\s*(\d+)y", r"x \s* \s*(\d+)\s*\s* y",
                                  r"x\ \s+\ \s*(\d+)y", "x  {page}  y"])
def test_adjacent_whitespace_runs_are_one_quantifier(tmpl):
    md = "<!-- p 1 -->\n" + SPACES * 20 + "<!-- p 2 -->\n"
    t = time.perf_counter()
    to_aidoc(md, "custom", expected_pages=2, marker_regex=tmpl)
    compile_marker(tmpl).findall(SPACES * 20 + "x" + " " * 3000 + "1" * 3000 + "z")
    assert time.perf_counter() - t < 2 * PER_CALL_S, tmpl


def test_merged_runs_keep_their_meaning():
    pat = compile_marker(r"p\s*\s+(\d+)\s\s*!")
    assert pat.findall("p 3 ! p4 ! p  5  !") == ["3", "5"]          # \s*\s+ needs one space; \s\s* too
    assert compile_marker("[[p {page}]]").findall("[[p 1]] [[p   2]] [[p3]]") == ["1", "2"]


_PIECES = ["x", "p", "a", "<", "-", ":", r"\s", r"\s*", r"\s+", " ", r"\ ", r"\-", "1", "0"]
_INPUTS = [SPACES * 5, "x" * 5000, ("x" + "1" * 50) * 200, "a" * 20000 + "!", ("p " * 3000) + "1" * 3000,
           ("x" + " \t" * 1500 + "1" * 7 + " " * 1500) * 3, "<- " * 4000 + ":"]


@pytest.mark.timeout(120)
def test_fuzzed_templates_run_in_linear_time():
    rng = random.Random(20261021)
    slow = []
    for _ in range(300):
        left = [rng.choice(_PIECES) for _ in range(rng.randint(0, 6))]
        right = [rng.choice(_PIECES) for _ in range(rng.randint(0, 6))]
        tmpl = rng.choice(["x", "p", "<", "a"]) + "".join(left) + rng.choice([r"(\d+)", r"([0-9]+)", r"(\d{1,9})"]) \
            + "".join(right)
        try:
            pat = compile_marker(tmpl)
        except ValueError:
            continue
        for s in _INPUTS:
            t = time.perf_counter()
            pat.findall(s)
            dt = time.perf_counter() - t
            if dt > PER_CALL_S:
                slow.append((tmpl, s[:20], round(dt, 2)))
    assert not slow, slow[:5]
