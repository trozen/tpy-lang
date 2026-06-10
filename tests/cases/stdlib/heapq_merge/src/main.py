# heapq.merge: lazily merge pre-sorted list[T] inputs into one sorted stream.
# Yields Own[T], so a reference-type result collects into a list cleanly.
import heapq
from tpy import Int32

class Item:
    key: Int32
    tag: Int32
    def __init__(self, key: Int32, tag: Int32) -> None:
        self.key = key
        self.tag = tag
    def __lt__(self, other: 'Item') -> bool:
        return self.key < other.key

def main() -> None:
    a: list[Int32] = [1, 4, 7]
    b: list[Int32] = [2, 5]
    c: list[Int32] = [3, 6, 8]
    r1: list[Int32] = []
    for x in heapq.merge(a, b, c):
        r1.append(x)
    print(r1)

    # some inputs empty
    empty: list[Int32] = []
    r2: list[Int32] = []
    for x in heapq.merge(empty, b, empty):
        r2.append(x)
    print(r2)

    # all inputs empty -> empty stream (post-init heap is empty)
    r2b: list[Int32] = []
    for x in heapq.merge(empty, empty):
        r2b.append(x)
    print(r2b)

    # single input
    one: list[Int32] = [5]
    r3: list[Int32] = []
    for x in heapq.merge(one):
        r3.append(x)
    print(r3)

    # duplicates across inputs
    d1: list[Int32] = [1, 1, 3]
    d2: list[Int32] = [1, 2]
    r4: list[Int32] = []
    for x in heapq.merge(d1, d2):
        r4.append(x)
    print(r4)

    # materialize the lazy result directly
    print(list(heapq.merge(a, b, c)))

    # reference-type elements: Own[T] yield collects into a list with no
    # implicit-copy warning; read back in merged order.
    ia: list[Item] = [Item(1, 10), Item(4, 40)]
    ib: list[Item] = [Item(2, 20), Item(3, 30)]
    merged: list[Item] = list(heapq.merge(ia, ib))
    for it in merged:
        print(it.key, it.tag)

main()
