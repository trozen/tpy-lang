# Function params are contravariant: a callback taking Int32 must NOT
# satisfy a contract that may pass None (the old covariant check accepted
# this and died in C++).
from typing import Callable
from tpy import Fn, Int32


def cb(x: Int32) -> None:
    print(x)


def use(f: Fn[[Int32 | None], None]) -> None:
    f(None)


def main() -> None:
    g: Callable[[Int32], None] = cb
    use(g)  # tpyc: error(/expected Fn\[\[Int32 \| None\], None\]/)


main()
