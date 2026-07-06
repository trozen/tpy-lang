# Test sort for list[T], ArrayList[T, N], Span[T], user-defined types, and
# stability; plus that a side-effecting sort receiver evaluates exactly once.
from __future__ import annotations
from tpy import Int32, Span
from tplib import ArrayList

class Pair:
    key: Int32
    tag: Int32

    def __init__(self, key: Int32, tag: Int32) -> None:
        self.key = key
        self.tag = tag

    def __lt__(self, other: Pair) -> bool:
        return self.key < other.key

    def __repr__(self) -> str:
        return str(self.key) + ":" + str(self.tag)

def test_list_sort() -> None:
    a: list[Int32] = [5, 3, 1, 4, 2]
    a.sort()
    print(a)

def test_arraylist_sort() -> None:
    a = ArrayList[Int32, 8]()
    a.append(5)
    a.append(3)
    a.append(1)
    a.append(4)
    a.append(2)
    a.sort()
    print(a)

def test_user_type_sort() -> None:
    a: list[Pair] = [Pair(3, 0), Pair(1, 0), Pair(2, 0)]
    a.sort()
    for p in a:
        print(p)

def test_stable_sort() -> None:
    a: list[Pair] = [
        Pair(2, 1),
        Pair(1, 1),
        Pair(2, 2),
        Pair(1, 2),
        Pair(2, 3),
    ]
    a.sort()
    for p in a:
        print(p)

def test_span_sort() -> None:
    # Sorting a Span mutates the aliased backing list (view, not a copy).
    lst: list[Int32] = [5, 3, 1, 4, 2]
    s: Span[Int32] = Span[Int32](lst)
    s.sort()
    print(lst)

def key() -> Int32:
    # A side-effecting subscript index: must run once per sort receiver.
    print("k")
    return 0

def test_sort_receiver_evaluated_once() -> None:
    rows: list[list[Int32]] = [[3, 1, 2]]
    rows[key()].sort()
    print(rows)

def main() -> None:
    test_list_sort()
    test_arraylist_sort()
    test_user_type_sort()
    test_stable_sort()
    test_span_sort()
    test_sort_receiver_evaluated_once()

main()
