from tpy import Int32


class Point:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x


x = None
y = None  # tpyc: error(/Cannot infer type for 'y': assigned None but never assigned a concrete value/)
x = Point(Int32(1))
