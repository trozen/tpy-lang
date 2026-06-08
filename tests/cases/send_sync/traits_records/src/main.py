# Send/Sync auto-derivation for user records: all-value fields, container
# fields, Callable fields (non-Send since the erased closure may capture
# borrowed state), and per-instantiation answers for generic records.
from tpy import Int32
from typing import Callable

class Point:
    x: Int32
    y: Int32

    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

class Bag:
    items: list[Int32]

    def __init__(self) -> None:
        self.items = []

class Handler:
    cb: Callable[[Int32], None]

    def __init__(self, cb: Callable[[Int32], None]) -> None:
        self.cb = cb

class Pair[T]:
    first: T
    second: T

    def __init__(self, first: T, second: T) -> None:
        self.first = first
        self.second = second

def main() -> None:
    p = Point(1, 2)             # tpyc: is_send(yes) is_sync(yes)
    b = Bag()                   # tpyc: is_send(yes) is_sync(no)
    h = Handler(lambda n: print(n))  # tpyc: is_send(no) is_sync(no)
    pv = Pair(1, 2)             # tpyc: is_send(yes) is_sync(yes)
    pl = Pair[list[Int32]]([1], [2])  # tpyc: is_send(yes) is_sync(no)
    print(p.x, p.y, len(b.items))
    h.cb(3)
    print(pv.first, len(pl.second))

main()
