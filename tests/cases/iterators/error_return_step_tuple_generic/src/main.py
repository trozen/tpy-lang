# Error: a tuple literal holding a generic generator's step, returned past
# its iteration, dangles: its reference element roots the step.
from typing import Iterator, Protocol
from tpy import copy, int32


class HasBump(Protocol):
    def bump(self) -> None: ...


class P:
    xs: list[int32]

    def __init__(self, n: int32) -> None:
        self.xs = [n]

    def bump(self) -> None:
        self.xs[0] += 1


def source[T](seed: T) -> Iterator[T]:
    p = copy(seed)
    yield p


def escape[T: HasBump](fallback: T) -> tuple[T, int32]:
    for p in source(fallback):
        p.bump()
        # the step escapes inside the returned tuple
        return (p, 1)  # tpyc: error(/Cannot return 'p': it borrows an element of a generator/)
    return (fallback, 0)


def main() -> None:
    p = P(7)
    x = escape(p)
    print(x[0].xs[0])


main()
