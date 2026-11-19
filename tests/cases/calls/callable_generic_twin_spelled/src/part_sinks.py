# The frame step an open `T` reaches at `T = Own[N]` takes the value at its
# last use, as the twin's `std::move(x)`: `N` is @nocopy, so a copy there
# would not compile. A tuple element holding `T` at `T = Own[N]` is refused
# (calls/error_own_type_arg_composite_leaf*); an inferred lending call is not.
from typing import Iterator
from tpy import Own, int32, nocopy


@nocopy
class N:
    xs: list[int32]

    def __init__(self, n: int32) -> None:
        self.xs = [n]


# a frame step
def gen_step[T](x: T) -> Iterator[T]:
    yield x  # tpyc: ok


def lend_pair[T](x: T) -> tuple[T, int32]:
    return (x, 1)


def via_lend[T](x: T) -> int32:
    # inferred inside the generic: the call lends at the storage form, so
    # `T = Own[N]` refuses nothing and copies nothing
    t = lend_pair(x)  # tpyc: ok
    return t[1]


def main() -> None:
    print("sink-lend", via_lend[Own[N]](N(3)))  # tpyc: ok
    for s in gen_step[Own[N]](N(5)):
        s.xs.append(6)
        print("sink-step", s.xs)
