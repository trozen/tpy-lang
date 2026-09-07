# A VALUE-record inner needs no form convert at an Optional field write: borrow
# and storage forms coincide, so the field takes the bare store.
from tpy import Int32, ValueType


class Point(ValueType):
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x


class Slot:
    o: Point | None

    def __init__(self) -> None:
        self.o = None

    def put(self, v: Point) -> None:
        self.o = v  # value-record inner: stored bare, no convert


def main() -> None:
    s = Slot()
    print(s.o is None)
    s.put(Point(5))
    if s.o is not None:
        print(s.o.x)


main()
