# Phase 20 Stage 3 sema rule: `raise <expr>` peels __deref__ until a
# non-Deref type is reached; the peeled type must be Throwable or a
# concrete BaseException subclass. A Box[T] where T is not a Throwable
# (e.g. a plain user class) is rejected with a targeted diagnostic.
from tpy import int32
from tplib import Box


class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y


def main() -> None:
    p: Box[Point] = Box(Point(1, 2))
    raise p  # tpyc: error(/is not Throwable/)


main()
