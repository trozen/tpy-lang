# Yielding an owning call's tuple at `tuple[Own[A], B]` is refused: moving A
# while lending B is not built (BUGS.md#mixed-tuple-move-and-lend-refused).
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


def mk() -> Own[tuple[A, B]]:
    return (A(1), B(2))


# the reject is located at the frame's def, not at the yield inside it
def gen() -> Iterator[tuple[Own[A], B]]:  # tpyc: error(/return\.copy_undeclared/)
    t = mk()
    yield t


def main() -> None:
    for q in gen():
        print(q[0].v, q[1].v)


main()
