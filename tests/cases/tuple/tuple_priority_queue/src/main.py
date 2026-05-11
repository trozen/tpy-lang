# Canonical priority-queue pattern: list[tuple[priority, payload]] with heapq.
# Requires tuple[T1, T2] to satisfy Comparable so heappush[T: Comparable]
# accepts the tuple element type.
from heapq import heappush, heappop
from tpy import Int32

def main() -> None:
    pq: list[tuple[Int32, str]] = []
    heappush(pq, (Int32(3), "third"))
    heappush(pq, (Int32(1), "first"))
    heappush(pq, (Int32(2), "second"))
    heappush(pq, (Int32(1), "tied_with_first"))

    while len(pq) > 0:
        prio, payload = heappop(pq)
        print(prio, payload)

main()
