# The overload an inferred float local matches seeds peek's argument, but an
# int list there stays list[int32] and matches no overload.
from tpy import Own, dispatch, int32


class Rec:
    def __init__(self, v: int32) -> None:
        self.v = v


def peek[T](r: Rec, xs: Own[list[T]]) -> Own[list[T]]:
    r.v += 1
    return xs


@dispatch
def tot2(xs: list[float]) -> float:
    return 1.5 if len(xs) == 0 else xs[0]


@dispatch
def tot2(s: str) -> str:
    return s


def rebind(a: int32) -> None:
    r = Rec(1)
    y = 0.5
    # The seed hints [a] but converts none of its ints.
    y = tot2(peek(r, [a]))  # tpyc: error(/No matching overload for tot2\(list\[int32\]\)/)
    print(y, r.v)


rebind(3)
