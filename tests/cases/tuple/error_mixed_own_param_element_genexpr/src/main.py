# A mutating method called through the OWNED element of a mixed owned+borrow
# tuple param from a generator expression (receiver chain `p[0].xs`):
# rejected at the lowering like a method call on an owned element of the
# fully owned twin (BUGS.md#consume-own-element-of-mixed-tuple).
from tpy import Own, int32


class Box:
    xs: list[int32]

    def __init__(self) -> None:
        self.xs = [1, 2]


def f(p: tuple[Own[Box], Box]) -> bool:
    return any(p[0].xs.pop() > 0 for _ in range(1))  # tpyc: error(/method\.recv\.field_chain/)


def main() -> None:
    b = Box()
    print(f((Box(), b)))


main()
