# Test dict(iterable) constructor from various iterable sources
from tpy import Int32

def from_list_of_tuples() -> None:
    pairs: list[tuple[str, Int32]] = [("a", 1), ("b", 2), ("c", 3)]
    d = dict(pairs)
    print(d)
    print(len(d))
    print(d["a"])
    print(d["c"])

def from_items_view() -> None:
    original: dict[str, Int32] = {"x": 10, "y": 20, "z": 30}
    d = dict(original.items())
    print(d)
    print(d["x"])
    print(d["z"])

def from_empty_list() -> None:
    pairs: list[tuple[str, Int32]] = []
    d = dict(pairs)
    print(d)
    print(len(d))

def with_int_keys() -> None:
    pairs: list[tuple[Int32, str]] = [(1, "one"), (2, "two")]
    d = dict(pairs)
    print(d)
    print(d[1])

def with_duplicate_keys() -> None:
    pairs: list[tuple[str, Int32]] = [("a", 1), ("b", 2), ("a", 99)]
    d = dict(pairs)
    print(d)
    print(d["a"])

from_list_of_tuples()
from_items_view()
from_empty_list()
with_int_keys()
with_duplicate_keys()
