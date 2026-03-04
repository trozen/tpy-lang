# Dict types conform to Iterable[T], passable to generic functions
from typing import Iterable
from tpy import Int32

def collect_items(items: Iterable[str]) -> None:
    for x in items:
        print(x)

def collect_ints(items: Iterable[Int32]) -> None:
    for x in items:
        print(x)

def collect_pairs(items: Iterable[tuple[str, Int32]]) -> None:
    for pair in items:
        k, v = pair
        print(k, v)

def main() -> None:
    d: dict[str, Int32] = {"a": 1, "b": 2, "c": 3}

    # dict itself is Iterable[K]
    print("keys via dict:")
    collect_items(d)

    # dict.keys() is Iterable[K]
    print("keys via keys():")
    collect_items(d.keys())

    # dict.values() is Iterable[V]
    print("values via values():")
    collect_ints(d.values())

    # dict.items() is Iterable[tuple[K, V]]
    print("items via items():")
    collect_pairs(d.items())

main()
