from tpy import Int32, Own


class Point:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x


def make_owned_point() -> Own[Point]:
    return Point(11)


x = None
x = make_owned_point()
print(x.x)
