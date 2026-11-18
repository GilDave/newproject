import pytest

from intervals import merge_intervals


def test_basic():
    assert merge_intervals([(1, 3), (2, 6), (8, 10)]) == [(1, 6), (8, 10)]


def test_touching_merges():
    assert merge_intervals([(1, 3), (3, 5)]) == [(1, 5)]


def test_adjacent_integers_do_not_merge():
    assert merge_intervals([(1, 2), (3, 4)]) == [(1, 2), (3, 4)]


def test_unsorted_input():
    assert merge_intervals([(8, 10), (1, 3), (2, 6)]) == [(1, 6), (8, 10)]


def test_contained_interval():
    assert merge_intervals([(1, 10), (2, 3), (4, 5)]) == [(1, 10)]


def test_duplicates():
    assert merge_intervals([(1, 2), (1, 2)]) == [(1, 2)]


def test_negative_numbers():
    assert merge_intervals([(-5, -1), (-3, 2)]) == [(-5, 2)]


def test_single_point_interval():
    assert merge_intervals([(4, 4)]) == [(4, 4)]


def test_empty():
    assert merge_intervals([]) == []


def test_input_is_not_mutated():
    data = [(5, 6), (1, 2), (2, 3)]
    snapshot = list(data)
    merge_intervals(data)
    assert data == snapshot


def test_returns_a_new_list_of_tuples():
    data = [(1, 2)]
    out = merge_intervals(data)
    assert out is not data and all(isinstance(x, tuple) for x in out)


def test_invalid_interval_raises():
    with pytest.raises(ValueError):
        merge_intervals([(1, 2), (5, 3)])


def test_invalid_interval_raises_even_if_it_would_be_swallowed_by_a_merge():
    with pytest.raises(ValueError):
        merge_intervals([(1, 10), (5, 3)])
