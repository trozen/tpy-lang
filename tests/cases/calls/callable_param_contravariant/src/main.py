# Contravariant params, covariant return: a callback accepting MORE
# (int32 | None) satisfies a contract passing less (int32). The old
# covariant check rejected this sound direction.
from typing import Callable
from tpy import Fn, int32


def cb(x: int32 | None) -> None:
    if x is None:
        print("none")
    else:
        print("got", x)


def use(f: Fn[[int32], None]) -> None:
    f(7)


def main() -> None:
    g: Callable[[int32 | None], None] = cb
    use(g)


main()
