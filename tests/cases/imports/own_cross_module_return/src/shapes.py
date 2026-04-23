# Defines a record + `Own[Record]`-returning factory for cross-module use.
from tpy import Int32, Own


class Circle:
    radius: Int32

    def __init__(self, radius: Int32) -> None:
        self.radius = radius


def make_circle(r: Int32) -> Own[Circle]:
    return Circle(r)
