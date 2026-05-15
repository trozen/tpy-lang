# heapq with reference-type T: regression guard for rvalue-arg acceptance on
# heappush/heappushpop/heapreplace (the `item: Own[T]` signature shape).
from heapq import heappush, heappop, heappushpop, heapreplace, heapify


class Box:
    val: int

    def __init__(self, v: int) -> None:
        self.val = v

    def __lt__(self, o: 'Box') -> bool:
        return self.val < o.val


def main() -> None:
    h: list[Box] = []
    # rvalue arg (regression target):
    heappush(h, Box(5))
    heappush(h, Box(3))
    heappush(h, Box(8))
    heappush(h, Box(1))
    # lvalue arg also works (implicit move of Own[T] argument):
    b = Box(4)
    heappush(h, b)
    out: list[int] = []
    while len(h) > 0:
        out.append(heappop(h).val)
    print(out)

    # heappushpop: empty-heap early-return -- item returned unchanged.
    h_empty: list[Box] = []
    print(heappushpop(h_empty, Box(7)).val)
    print(len(h_empty))

    # heappushpop: smaller-than-root returns item unchanged
    h2: list[Box] = [Box(2), Box(4), Box(6)]
    heapify(h2)
    print(heappushpop(h2, Box(1)).val)
    # heappushpop: larger-than-root swaps
    print(heappushpop(h2, Box(5)).val)
    print(h2[0].val)

    # heapreplace: pop root, then push
    h3: list[Box] = [Box(1), Box(3), Box(5)]
    heapify(h3)
    print(heapreplace(h3, Box(10)).val)
    print(h3[0].val)

    # heapreplace on a single-element heap
    h4: list[Box] = [Box(42)]
    print(heapreplace(h4, Box(99)).val)
    print(h4[0].val)


main()
