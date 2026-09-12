# Defines a record + `Own[Record]`-returning factory for cross-module use.
from tpy import int32, Own


class Circle:
    radius: int32

    def __init__(self, radius: int32) -> None:
        self.radius = radius


def make_circle(r: int32) -> Own[Circle]:
    return Circle(r)
