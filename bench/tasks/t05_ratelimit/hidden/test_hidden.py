import pytest

from ratelimit import SlidingWindowLimiter as Limiter


@pytest.mark.parametrize("limit,window", [(0, 10), (-1, 10), (2.5, 10), (2, 0), (2, -5), ("2", 10), (2, "10")])
def test_bad_arguments(limit, window):
    with pytest.raises(ValueError):
        Limiter(limit, window)


def test_basic_limit():
    lim = Limiter(2, 10)
    assert lim.allow("k", 0)
    assert lim.allow("k", 1)
    assert not lim.allow("k", 2)


def test_event_expires_exactly_at_window_end():
    lim = Limiter(2, 10)
    assert lim.allow("k", 0)
    assert lim.allow("k", 1)
    assert not lim.allow("k", 9.999)
    assert lim.allow("k", 10)       # the event at 0 stops counting at exactly 0 + 10
    assert not lim.allow("k", 10.5)  # events at 1 and 10 count
    assert lim.allow("k", 11)       # the event at 1 stops counting at 11


def test_rejected_requests_are_not_recorded():
    lim = Limiter(1, 10)
    assert lim.allow("k", 0)
    assert not lim.allow("k", 5)
    assert lim.allow("k", 10)


def test_keys_are_independent():
    lim = Limiter(1, 10)
    assert lim.allow("a", 0)
    assert lim.allow("b", 0)
    assert not lim.allow("a", 1)


def test_time_going_backwards_for_the_same_key_raises():
    lim = Limiter(5, 10)
    lim.allow("k", 100)
    with pytest.raises(ValueError):
        lim.allow("k", 99)
    assert lim.allow("other", 1)


def test_same_timestamp_is_allowed():
    lim = Limiter(3, 10)
    assert lim.allow("k", 5) and lim.allow("k", 5) and lim.allow("k", 5)
    assert not lim.allow("k", 5)


def test_float_window():
    lim = Limiter(1, 0.5)
    assert lim.allow("k", 0)
    assert not lim.allow("k", 0.25)
    assert lim.allow("k", 0.5)
