# heapq.merge yields Own[T] -- owned COPIES, not the source objects. TPy-only
# (CPython aliases): mutating a source element after merging leaves the
# collected results unchanged. The list-index tie-break also makes equal-keyed
# distinct objects emit in input order (stable), observable here because the
# two key-1 items carry different tags.
import heapq
from tpy import int32

class Item:
    key: int32
    tag: int32
    def __init__(self, key: int32, tag: int32) -> None:
        self.key = key
        self.tag = tag
    def __lt__(self, other: 'Item') -> bool:
        return self.key < other.key

def main() -> None:
    a: list[Item] = [Item(1, 10), Item(2, 20)]
    b: list[Item] = [Item(1, 99)]
    merged: list[Item] = list(heapq.merge(a, b))
    # stability: equal keys emit in input order -- a's (1,10) before b's (1,99)
    for it in merged:
        print(it.key, it.tag)
    # copy semantics: mutating the source does not touch the collected copies
    a[0].tag = 1000
    print("after source mutate:", merged[0].tag)

main()
