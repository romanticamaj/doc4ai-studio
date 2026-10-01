from aidoc.mcp.ratelimit import RateLimiter


def test_burst_then_refill():
    rl = RateLimiter(lambda: 60)
    t = 1000.0
    for _ in range(60):
        assert rl.acquire("k", now=t) == (True, 0)
    ok, retry = rl.acquire("k", now=t)
    assert ok is False and retry == 1                       # 1 token per second at 60/min
    assert rl.acquire("k", now=t + 1.0)[0] is True
    assert rl.acquire("k", now=t + 1.0)[0] is False
    assert rl.acquire("k", now=t + 61.0) == (True, 0)        # fully refilled after a minute


def test_per_token_override_and_isolation():
    rl = RateLimiter(lambda: 60)
    assert rl.acquire("slow", per_min=2, now=0.0)[0] and rl.acquire("slow", per_min=2, now=0.0)[0]
    ok, retry = rl.acquire("slow", per_min=2, now=0.0)
    assert ok is False and retry == 30                      # 2/min → one token every 30 s
    assert rl.acquire("other", now=0.0)[0] is True          # another key unaffected


def test_default_is_read_live():
    limit = {"v": 1}
    rl = RateLimiter(lambda: limit["v"])
    assert rl.acquire("k", now=0.0)[0] and not rl.acquire("k", now=0.0)[0]
    limit["v"] = 5
    rl.forget("k")
    assert all(rl.acquire("k", now=0.0)[0] for _ in range(5))


def test_retry_after_is_at_least_one_second():
    rl = RateLimiter(lambda: 100000)
    for _ in range(100000):
        rl.acquire("k", now=0.0)
    assert rl.acquire("k", now=0.0)[1] >= 1
