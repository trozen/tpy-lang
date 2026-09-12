# Function params are contravariant: a callback taking int32 must NOT
# satisfy a contract that may pass None (the old covariant check accepted
# this and died in C++).
from typing import Callable
from tpy import Fn, int32


def cb(x: int32) -> None:
    print(x)


def use(f: Fn[[int32 | None], None]) -> None:
    f(None)


def main() -> None:
    g: Callable[[int32], None] = cb
    use(g)  # tpyc: error(/expected Fn\[\[int32 \| None\], None\]/)


main()
