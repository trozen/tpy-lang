# A loop element passed to a @native writer reaches the loop's source: the
# source binds mutable and the loop binding `auto&&`.
__tpy__ = {"includes": ["native_types.hpp"]}
from tpy import Own, int32
from tpy.extern import native


class P:
    def __init__(self, n: int32) -> None:
        self.n = n


@native("nw::bump")
def native_bump(p: P) -> None: ...


@native("nw::bumped")
def native_bumped(p: P) -> int32: ...


def ns(ps: list[P]) -> Own[list[int32]]:
    return [p.n for p in ps]


# a for loop
def loop(ps: list[P]) -> None:
    for p in ps:
        native_bump(p)  # tpyc: ok


# a comprehension
def comp(ps: list[P]) -> Own[list[int32]]:
    return [native_bumped(p) for p in ps]  # tpyc: ok


def main() -> None:
    ps = [P(1), P(2)]
    loop(ps)
    print("loop:", ns(ps))
    print("comp:", comp(ps), ns(ps))


main()
