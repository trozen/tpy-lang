# Rebinding an existing reference-type local (or param) via a for-loop is
# rejected: the hoisted assignment cannot express CPython's aliasing.
from tpy import Int32


class Point:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x


def local_rebind(items: list[Point]) -> None:
    n = Point(0)
    print(n.x)
    for n in items:  # tpyc: error(/for-loop rebind of reference-type variable 'n'/)
        print(n.x)


def main() -> None:
    local_rebind([Point(1)])


main()
