# A Callable field is an opaque, potentially-mutating callee: a callback
# that structurally mutates its reference arg compiles (the std::function
# signature is non-const for non-readonly params), the mutation is visible
# after the call, and the calling method is inferred non-const.
from typing import Callable
from tpy import Int32


def add_one(xs: list[Int32]) -> None:
    xs.append(1)


class Holder:
    data: list[Int32]
    cb: Callable[[list[Int32]], None]

    def __init__(self, cb: Callable[[list[Int32]], None]) -> None:
        self.data = [0]
        self.cb = cb

    def poke(self) -> None:
        self.cb(self.data)


def main() -> None:
    h = Holder(add_one)
    h.poke()
    h.poke()
    print(len(h.data))
    print(h.data[1], h.data[2])


main()
