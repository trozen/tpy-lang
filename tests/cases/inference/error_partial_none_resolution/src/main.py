from tpy import int32


class Point:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


x = None
y = None  # tpyc: error(/Cannot infer type for 'y': assigned None but never assigned a concrete value/)
x = Point(int32(1))
