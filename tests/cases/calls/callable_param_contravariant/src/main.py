# Contravariant params, covariant return: a callback accepting MORE
# (Int32 | None) satisfies a contract passing less (Int32). The old
# covariant check rejected this sound direction.
from typing import Callable
from tpy import Fn, Int32


def cb(x: Int32 | None) -> None:
    if x is None:
        print("none")
    else:
        print("got", x)


def use(f: Fn[[Int32], None]) -> None:
    f(7)


def main() -> None:
    g: Callable[[Int32 | None], None] = cb
    use(g)


main()
