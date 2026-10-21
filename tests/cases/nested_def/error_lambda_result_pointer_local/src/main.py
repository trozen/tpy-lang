# A lambda's by-value record result over a pointer-bound local would copy the
# object the pointer names, a copy sema never declared
# (BUGS.md#lambda-body-partial-return-checks).
from typing import Callable
from tpy import int32


class P:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


def f(xs: list[P], c: bool) -> Callable[[], P]:
    q = xs[0] if c else None
    assert q is not None
    # `q` names an element of the caller's list.
    return lambda: q  # tpyc: error(/expr\.lambda:lambda\.result/)


def main() -> None:
    xs = [P(1)]
    print(f(xs, True)().v)


main()
