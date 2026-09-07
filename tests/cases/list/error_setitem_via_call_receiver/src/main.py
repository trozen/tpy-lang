# A container-returning CALL used as a subscript WRITE receiver: the setitem
# and `del` targets take name/field receivers only. The READ position through
# the same call receiver is pinned by tests/cases/list/subscript_scalar_read.
from tpy import Int32


class Box:
    xs: list[Int32]

    def __init__(self) -> None:
        self.xs = [1, 2, 3]

    def get(self) -> list[Int32]:
        return self.xs


def write_through_call(b: Box) -> None:
    b.get()[0] = 9  # tpyc: error(/setitem\.recv\.call/)


def main() -> None:
    b = Box()
    write_through_call(b)
    print(b.xs)


main()
