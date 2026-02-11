from tpy import Int32


class Point:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x


def bad() -> None:
    x = None  # tpyc: error(/incompatible with later annotation 'Point' at line 13/)
    x: Point = Point(1)


bad()
