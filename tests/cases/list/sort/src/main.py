# Test sort for list[T], ArrayList[T, N], user-defined types, and stability
from __future__ import annotations
from tpy import Int32
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

def main() -> None:
    test_list_sort()
    test_arraylist_sort()
    test_user_type_sort()
    test_stable_sort()

main()
