from recoup_gateway.ratelimit import RateLimiter


def test_bucket_depletes() -> None:
    rl = RateLimiter(per_minute=3)
    assert all(rl.allow("t") for _ in range(3))
    assert not rl.allow("t")
    assert rl.allow("other")
