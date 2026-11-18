import pytest

from roman import from_roman, to_roman


@pytest.mark.parametrize(
    "n,s",
    [(1, "I"), (4, "IV"), (9, "IX"), (14, "XIV"), (40, "XL"), (90, "XC"), (400, "CD"),
     (900, "CM"), (1994, "MCMXCIV"), (2024, "MMXXIV"), (3999, "MMMCMXCIX")],
)
def test_known_values(n, s):
    assert to_roman(n) == s
    assert from_roman(s) == n


def test_round_trip_every_value():
    for n in range(1, 4000):
        assert from_roman(to_roman(n)) == n


@pytest.mark.parametrize("n", [0, -1, 4000, 10000])
def test_to_roman_out_of_range(n):
    with pytest.raises(ValueError):
        to_roman(n)


@pytest.mark.parametrize("value", [3.5, "5", None, [1]])
def test_to_roman_type_error(value):
    with pytest.raises(TypeError):
        to_roman(value)


@pytest.mark.parametrize("s", ["IIII", "VX", "IC", "IIX", "MMMM", "", "iv", "XXXX", "VV", "IL", "XM", "CMC"])
def test_from_roman_rejects_non_standard(s):
    with pytest.raises(ValueError):
        from_roman(s)
