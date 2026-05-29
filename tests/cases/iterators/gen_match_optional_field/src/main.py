# H1: binding a pointer-repr `Optional` field (`maybe: Inner | None`) in a
# generator `match`, read across a yield. The binding bridges the optional
# field (storage form) to the `T*` frame slot via optional_to_ptr; the lvalue
# subject (param) keeps the pointer stable across the suspension.
from typing import Iterator, Optional


class Inner:
    n: int

    def __init__(self, n: int) -> None:
        self.n = n


class Box:
    maybe: Optional[Inner]

    def __init__(self, m: Optional[Inner]) -> None:
        self.maybe = m


def gen(b: Box) -> Iterator[int]:
    match b:
        case Box(maybe=v):
            yield 1
            if v is not None:
                print(v.n)
            yield 2


def main() -> None:
    for x in gen(Box(Inner(7))):
        print(x)
    print("--")
    for x in gen(Box(None)):
        print(x)


main()
