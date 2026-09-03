# The user-visible outcome of a body THIR cannot lower: a compile error naming
# the blocking construct's tag. The shape here is a deliberate refusal -- a
# local declared in the enclosing function and rebound to a fresh object inside
# a nested def has no stable home to hoist its slot into.
from tpy import Int32


class Point:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x


def main() -> None:  # tpyc: error(/not yet supported by C\+\+ code generation/)
    p: Point | None = None
    p = Point(1)

    def reset() -> None:
        nonlocal p
        p = Point(2)  # the rebind with no slot home

    reset()
    if p is not None:
        print(p.x)


main()
