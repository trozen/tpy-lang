# A freshly-built Own[...] rvalue (Rc.new(...), Box(...)) passed directly to a
# borrow param: the materialized temp outlives the call, so create-lend-drop
# is safe for any Own-rvalue.
from tpy import int32
from tplib import Rc, Box


class Counter:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def value(self) -> int32:
        return self.n


def read_rc(c: Rc[Counter]) -> int32:
    return c.get().value()


def read_box(c: Box[Counter]) -> int32:
    return c.get().value()


def main() -> None:
    print(read_rc(Rc.new(Counter(3))))    # tpyc: ok
    print(read_box(Box(Counter(4))))       # tpyc: ok


main()
