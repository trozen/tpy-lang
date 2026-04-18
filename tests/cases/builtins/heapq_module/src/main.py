# Tests for heapq module: push, pop, heapify, heapreplace, nsmallest, nlargest
import heapq
from tpy import Int32

def main() -> None:
    # Basic push/pop: always pops the minimum
    h: list[Int32] = []
    heapq.heappush(h, 5)
    heapq.heappush(h, 2)
    heapq.heappush(h, 8)
    heapq.heappush(h, 1)
    heapq.heappush(h, 4)
    print(heapq.heappop(h))  # 1
    print(heapq.heappop(h))  # 2
    print(heapq.heappop(h))  # 4

    # heapify converts an arbitrary list into a heap
    lst: list[Int32] = [9, 3, 7, 1, 5]
    heapq.heapify(lst)
    print(heapq.heappop(lst))  # 1
    print(heapq.heappop(lst))  # 3

    # heapreplace: pop min and push new item atomically
    h2: list[Int32] = [1, 3, 5]
    heapq.heapify(h2)
    old = heapq.heapreplace(h2, 2)
    print(old)  # 1
    print(heapq.heappop(h2))  # 2

    # nsmallest
    data: list[Int32] = [5, 1, 8, 2, 9, 3]
    small = heapq.nsmallest(3, data)
    print(small[0])  # 1
    print(small[1])  # 2
    print(small[2])  # 3

    # nlargest
    big = heapq.nlargest(3, data)
    print(big[0])  # 9
    print(big[1])  # 8
    print(big[2])  # 5

main()
