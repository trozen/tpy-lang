# Error: binding a pointer-repr `Optional` field from a NON-lvalue match
# subject (a constructed temporary) inside a generator would alias the
# dispatch-local subject copy and dangle across the suspension -- rejected
# with a clean diagnostic. Workaround: bind the subject to a local first.
from typing import Iterator, Optional


class Inner:
    n: int

    def __init__(self, n: int) -> None:
        self.n = n


class Box:
    maybe: Optional[Inner]

    def __init__(self, m: Optional[Inner]) -> None:
        self.maybe = m


def gen() -> Iterator[int]:
    match Box(Inner(7)):  # tpyc: error(/would dangle across a suspension/)
        case Box(maybe=v):
            yield 1
            if v is not None:
                print(v.n)
            yield 2


def main() -> None:
    for x in gen():
        print(x)


main()
