# Error: a nested `match` whose subject is a name narrowed by the outer arm
# renders to a dispatch-local extraction alias (`__case_N`), not a frame
# field -- so a pointer-repr `Optional` field binding from it would dangle
# across the suspension. The syntactic lvalue check sees a plain name, so the
# reject keys off the narrowing too. Workaround: bind the narrowed value to a
# local first.
from typing import Iterator, Optional


class Inner:
    n: int
    def __init__(self, n: int) -> None:
        self.n = n


class A:
    maybe: Optional[Inner]
    def __init__(self, m: Optional[Inner]) -> None:
        self.maybe = m


class B:
    x: int
    def __init__(self, x: int) -> None:
        self.x = x


def gen(u: A | B) -> Iterator[int]:
    match u:
        case A():
            match u:  # tpyc: error(/would dangle across a suspension/)
                case A(maybe=v):
                    yield 1
                    if v is not None:
                        print(v.n)
                    yield 2
        case B():
            yield 3


def main() -> None:
    for x in gen(A(Inner(5))):
        print(x)


main()
