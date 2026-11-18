import pytest

from csvline import parse_csv_line as parse


def test_simple():
    assert parse("a,b,c") == ["a", "b", "c"]


def test_single_field():
    assert parse("abc") == ["abc"]


def test_empty_fields():
    assert parse("a,,b") == ["a", "", "b"]


def test_trailing_comma_gives_empty_last_field():
    assert parse("a,b,") == ["a", "b", ""]


def test_leading_comma():
    assert parse(",a") == ["", "a"]


def test_empty_line():
    assert parse("") == [""]


def test_quoted_comma():
    assert parse('"a,b",c') == ["a,b", "c"]


def test_escaped_quote():
    assert parse('"he said ""hi""",x') == ['he said "hi"', "x"]


def test_quoted_empty_field():
    assert parse('"",a') == ["", "a"]


def test_unquoted_spaces_are_preserved():
    assert parse(" a , b ") == [" a ", " b "]


def test_quoted_spaces_are_preserved():
    assert parse('" a ",b') == [" a ", "b"]


def test_quoted_field_ending_with_an_escaped_quote():
    assert parse('"a"""') == ['a"']


@pytest.mark.parametrize("line", ['"abc', '"a"b', 'ab"c', ' "a"', '"a" ,b', '"a""', 'a,"b'])
def test_invalid(line):
    with pytest.raises(ValueError):
        parse(line)
