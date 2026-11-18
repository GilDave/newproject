import pytest

from lru import LRUCache


@pytest.mark.parametrize("cap", [0, -1, 2.0, "3", None])
def test_bad_capacity(cap):
    with pytest.raises(ValueError):
        LRUCache(cap)


def test_get_and_put():
    c = LRUCache(2)
    c.put("a", 1)
    c.put("b", 2)
    assert c.get("a") == 1 and c.get("b") == 2 and c.get("z") is None


def test_evicts_least_recently_used():
    c = LRUCache(2)
    c.put("a", 1)
    c.put("b", 2)
    c.put("c", 3)
    assert "a" not in c and "b" in c and "c" in c and len(c) == 2


def test_get_refreshes_recency():
    c = LRUCache(2)
    c.put("a", 1)
    c.put("b", 2)
    c.get("a")
    c.put("c", 3)
    assert "a" in c and "b" not in c


def test_put_existing_updates_refreshes_and_does_not_grow():
    c = LRUCache(2)
    c.put("a", 1)
    c.put("b", 2)
    c.put("a", 10)
    assert len(c) == 2
    c.put("c", 3)
    assert c.get("a") == 10 and "b" not in c


def test_contains_does_not_refresh():
    c = LRUCache(2)
    c.put("a", 1)
    c.put("b", 2)
    assert "a" in c
    c.put("c", 3)
    assert "a" not in c


def test_none_value_is_storable_and_get_refreshes():
    c = LRUCache(2)
    c.put("a", None)
    c.put("b", 2)
    assert "a" in c
    assert c.get("a") is None
    c.put("c", 3)
    assert "a" in c and "b" not in c


def test_keys_order_oldest_first():
    c = LRUCache(3)
    c.put("a", 1)
    c.put("b", 2)
    c.put("c", 3)
    c.get("a")
    assert c.keys() == ["b", "c", "a"]


def test_capacity_one():
    c = LRUCache(1)
    c.put("a", 1)
    c.put("b", 2)
    assert "a" not in c and c.get("b") == 2
