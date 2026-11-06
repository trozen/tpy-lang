# The user-visible outcome of a body THIR cannot lower: a compile error naming
# the blocking construct's tag. The shape here is a filed gap -- a field read
# off a record ternary (BUGS.md#reference-ternary-position-gaps).
from tpy import int32


class Point:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


def main(c: bool) -> None:
    a = Point(1)
    b = Point(2)
    print((a if c else b).x)  # tpyc: error(/not yet supported by C\+\+ code generation/)


main(True)
