# A container-returning CALL used as a subscript WRITE receiver: the setitem
# and `del` targets take name/field receivers only. The READ position through
# the same call receiver is pinned by tests/cases/list/subscript_scalar_read.
# A `@property` read is such a call, so `b.items[0] = 5` takes the SAME reject
# one spelling over -- it used to stop at the field-receiver gate instead. The
# property leg below is unannotated: the compile stops at the first error.
from tpy import int32


class Box:
    xs: list[int32]

    def __init__(self) -> None:
        self.xs = [1, 2, 3]

    def get(self) -> list[int32]:
        return self.xs

    @property
    def items(self) -> list[int32]:
        return self.xs


def write_through_call(b: Box) -> None:
    b.get()[0] = 9  # tpyc: error(/setitem\.recv\.call/)


def write_through_property(b: Box) -> None:
    b.items[0] = 5


def main() -> None:
    b = Box()
    write_through_call(b)
    write_through_property(b)
    print(b.xs)


main()
