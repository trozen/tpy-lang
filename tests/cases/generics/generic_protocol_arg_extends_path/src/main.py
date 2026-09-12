# Regression: implicit-T inference where the conformance comes through
# the *extends* path (record explicitly inherits a generic protocol)
# rather than structural method-match, with the arg arriving Ref-wrapped
# via a generic outer parameter. Exercises the unwrap in
# `get_extends_protocol_type_arg`, parallel to the structural-path test
# in generic_protocol_arg_from_param/.
from typing import Iterable, Protocol
from tpy import int32, Own


class MyIter[T]:
    items: list[T]
    pos: int32

    def __init__(self, items: list[T]) -> None:
        self.items = items
        self.pos = 0

    def __next__(self) -> T:
        if self.pos >= len(self.items):
            raise StopIteration
        v = self.items[self.pos]
        self.pos += 1
        return v


# Explicit `Iterable[T]` parent populates `extends_protocols`, routing
# inference for `length[T](xs: Iterable[T])` through the extends path
# rather than structural method-match.
class MyList[T](Iterable[T]):
    items: list[T]

    def __init__(self) -> None:
        self.items = []

    def add(self, x: T) -> None:
        self.items.append(x)

    def __iter__(self) -> Own[MyIter[T]]:
        return MyIter[T](self.items)


def length[T](xs: Iterable[T]) -> int32:
    n: int32 = 0
    for _ in xs:
        n += 1
    return n


def total_of[T](xs: MyList[T]) -> int32:
    # Generic outer T -- forces Ref[MyList[T]] at the call site.
    # `length[T]` infers via `Iterable[T]`; conformance is via the
    # extends declaration on MyList, so the path goes through
    # `get_extends_protocol_type_arg` with a Ref-wrapped arg.
    return length(xs)


def main() -> None:
    xs = MyList[int32]()
    xs.add(3)
    xs.add(4)
    xs.add(5)
    print(total_of(xs))   # 3


main()
