# A lending yield of a value that dies with the statement: `pick` returns a
# reference into its argument, a temporary list, so the reference the yield
# hands out would dangle at the next resumption.
from typing import Iterator
from tpy import Own, int32


class A:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


def pick(xs: list[A]) -> A:
    return xs[0]


def make_list() -> Own[list[A]]:
    return [A(1), A(2)]


def gen() -> Iterator[A]:
    # the subject: the reference outlives the list it points into
    yield pick(make_list())  # tpyc: error(/Cannot yield local or temporary as reference/)
    yield pick(make_list())


def main() -> None:
    for a in gen():
        print(a.v)


main()
