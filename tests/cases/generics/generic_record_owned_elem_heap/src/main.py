# Generic record (Entry[T]) whose type param is bound from a generic list[T]
# subscript via copy(), then pushed through heapq.heappush inside a generic
# function -- the index-heap shape that defeated ctor type-arg inference
# (the arg's element borrow form leaked as Entry[Ref[T]], conflicting with the
# heap's Entry[T] and failing heappush's inference).
from tpy import Comparable, Own, copy
import heapq


class Entry[T: Comparable]:
    val: T
    idx: int

    def __init__(self, val: Own[T], idx: int) -> None:
        self.val = val
        self.idx = idx

    def __lt__(self, other: "Entry[T]") -> bool:
        return self.val < other.val


def heap_sort[T: Comparable](src: list[T]) -> Own[list[T]]:
    heap: list[Entry[T]] = []
    i = 0
    while i < len(src):
        # T inferred for Entry/heappush must be Entry[T], not Entry[Ref[T]].
        heapq.heappush(heap, Entry(copy(src[i]), i))
        i += 1
    out: list[T] = []
    while len(heap) > 0:
        top = heapq.heappop(heap)  # tpyc: type(/Entry\[T\]/)
        out.append(copy(top.val))
    return out


def main() -> None:
    nums: list[int] = [5, 1, 4, 2, 3]
    print(heap_sort(nums))
    words: list[str] = ["pear", "apple", "kiwi", "fig"]
    print(heap_sort(words))


main()
