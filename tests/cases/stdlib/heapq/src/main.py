# heapq: core min-heap ops, nsmallest/nlargest, int and str element types.
# Tuple priority queue covered separately in cases/tuple/tuple_priority_queue.
# Reference-type heaps still blocked on generic list.pop() for ref types;
# see lib/tpy/heapq.py header for details.
from heapq import heappush, heappop, heapify, heappushpop, heapreplace, nsmallest, nlargest

def main() -> None:
    h: list[int] = []
    for v in [5, 3, 8, 1, 4, 7, 2, 6]:
        heappush(h, v)
    out: list[int] = []
    while len(h) > 0:
        out.append(heappop(h))
    print(out)

    data = [9, 5, 6, 2, 3, 1, 8, 4, 7]
    heapify(data)
    print(data[0])

    # heappushpop: smaller-than-root returns item unchanged
    h2 = [1, 3, 5]
    heapify(h2)
    print(heappushpop(h2, 0))
    # heappushpop: larger-than-root pushes then pops root
    print(heappushpop(h2, 4))
    print(h2[0])
    # heappushpop: item equal to root -- no swap, item returned unchanged
    h2eq = [2, 4, 6]
    heapify(h2eq)
    print(heappushpop(h2eq, 2))
    print(h2eq[0])

    # heapreplace: always pop root then push
    h3 = [1, 3, 5]
    heapify(h3)
    print(heapreplace(h3, 10))
    print(h3[0])

    # 1-element heap round trip
    h4: list[int] = []
    heappush(h4, 42)
    print(heappop(h4))
    print(len(h4))

    nums = [9, 2, 7, 1, 8, 4, 6, 3, 5]
    print(nsmallest(3, nums))
    print(nlargest(3, nums))
    # n >= len returns the whole collection
    print(nsmallest(20, [3, 1, 2]))
    print(nlargest(20, [3, 1, 2]))
    # n == 0 returns []
    print(nsmallest(0, [3, 1, 2]))
    print(nlargest(0, [3, 1, 2]))

    # str heap: Comparable via <; lexicographic pop order.
    words: list[str] = []
    heappush(words, "cherry")
    heappush(words, "apple")
    heappush(words, "banana")
    while len(words) > 0:
        print(heappop(words))

main()
