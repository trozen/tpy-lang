# A record method's `Own[T]` param is a by-value `T&&` slot, so a non-movable
# lvalue there has to hoist a copy temp and bind the move -- which needs a
# statement to flush the temp into. Nested inside another call there is none,
# so the shape must keep rejecting: admitting it emits `s.take(b)` with an
# lvalue in a `Point&&` slot, which does not compile.
from tpy import Own


class Point:
    x: int

    def __init__(self, x: int) -> None:
        self.x = x


class Sink:
    held: Point

    def __init__(self) -> None:
        self.held = Point(0)

    def take(self, p: Own[Point]) -> int:
        self.held = p
        return self.held.x


def main() -> None:
    s = Sink()
    b = Point(3)
    print(str(s.take(b)))  # tpyc: error(/method.arg_shape/)
    print(b.x)


main()
