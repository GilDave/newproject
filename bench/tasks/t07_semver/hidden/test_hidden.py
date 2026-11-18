import pytest

from semver import compare_versions as cmp

CHAIN = [
    "1.0.0-alpha",
    "1.0.0-alpha.1",
    "1.0.0-alpha.beta",
    "1.0.0-beta",
    "1.0.0-beta.2",
    "1.0.0-beta.11",
    "1.0.0-rc.1",
    "1.0.0",
    "1.9.0",
    "1.10.0",
    "1.11.0",
    "2.0.0",
]


def test_official_precedence_chain():
    for i in range(len(CHAIN)):
        for j in range(len(CHAIN)):
            expected = (i > j) - (i < j)
            assert cmp(CHAIN[i], CHAIN[j]) == expected, (CHAIN[i], CHAIN[j])


def test_equal():
    assert cmp("1.2.3", "1.2.3") == 0


def test_build_metadata_is_ignored():
    assert cmp("1.0.0+a", "1.0.0+b") == 0
    assert cmp("1.0.0-rc.1+x", "1.0.0-rc.1") == 0
    assert cmp("1.0.0+zzz", "1.0.1") == -1


def test_numeric_not_lexicographic():
    assert cmp("1.0.0-alpha.10", "1.0.0-alpha.9") == 1
    assert cmp("10.0.0", "9.0.0") == 1


def test_numeric_identifier_is_lower_than_alphanumeric():
    assert cmp("1.0.0-1", "1.0.0-a") == -1
    assert cmp("1.0.0-alpha.1", "1.0.0-alpha.a") == -1


def test_hyphen_inside_prerelease_identifier():
    assert cmp("1.0.0-alpha-1", "1.0.0-alpha-2") == -1


def test_zero_is_allowed():
    assert cmp("0.0.0", "0.0.1") == -1


def test_returns_plain_ints():
    assert type(cmp("1.0.0", "1.0.0")) is int


@pytest.mark.parametrize(
    "bad",
    ["1.0", "1.0.0.0", "01.0.0", "1.00.0", "v1.0.0", "", "1.0.0-", "1.0.0-01", "1.0.0-alpha..1", "1.0.0+", "1.0.0-a_b"],
)
def test_invalid_versions(bad):
    with pytest.raises(ValueError):
        cmp(bad, "1.0.0")
    with pytest.raises(ValueError):
        cmp("1.0.0", bad)


@pytest.mark.parametrize("value", [1, None, b"1.0.0"])
def test_non_string_is_type_error(value):
    with pytest.raises(TypeError):
        cmp(value, "1.0.0")
