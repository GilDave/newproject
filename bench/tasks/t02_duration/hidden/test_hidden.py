import pytest

from duration import parse_duration


@pytest.mark.parametrize(
    "text,expected",
    [
        ("1h30m", 5400),
        ("2d", 172800),
        ("45s", 45),
        ("1d 2h 3m 4s", 93784),
        ("30m1h", 5400),
        ("1H30M", 5400),
        ("  5m  ", 300),
        ("0s", 0),
        ("90m", 5400),
        ("1h 30m", 5400),
    ],
)
def test_valid(text, expected):
    assert parse_duration(text) == expected


@pytest.mark.parametrize(
    "text", ["90", "", "   ", "5x", "-5s", "1.5h", "h", "1h1h", "1h 1H", "1h,30m", "1h30", "m5"]
)
def test_invalid_values(text):
    with pytest.raises(ValueError):
        parse_duration(text)


@pytest.mark.parametrize("value", [90, None, b"5s", ["5s"]])
def test_non_string_is_type_error(value):
    with pytest.raises(TypeError):
        parse_duration(value)


def test_returns_int():
    assert type(parse_duration("1m")) is int
