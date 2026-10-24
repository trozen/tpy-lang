# A match arm capturing a union element a generator lends out of its
# frame's tuple is refused, a read as much as a write: the element is the
# tuple's const pointer variant, which a capture cannot bind
# (BUGS.md#lent-union-tuple-element-capture-ill-formed). A type-only arm
# compiles (tuple/tuple_converts_per_element).
from typing import Iterator
from tpy import Own, int32


class A:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class B:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class C:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


def mk_abc() -> Own[tuple[A, B | C]]:
    return (A(1), B(2))


def move_lend_union() -> Iterator[tuple[Own[A], B | C]]:
    t = mk_abc()
    yield t


def main() -> None:
    for q4 in move_lend_union():
        match q4[1]:
            case B() as b4:  # tpyc: error(/capture .b4. would bind a union element/)
                # the capture is the refused shape
                print("move_lend_union", q4[0].v, b4.v)
            case _:
                pass


main()
