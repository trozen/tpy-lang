from tpy import int32


class Point:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


def bad() -> None:
    x = None  # tpyc: error(/incompatible with later annotation 'Point' at line 13/)
    x: Point = Point(1)


bad()
