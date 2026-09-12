# @property getter-only (read-only property) and computed property
from tpy import int32

class Circle:
    _radius: int32

    def __init__(self, radius: int32) -> None:
        self._radius = radius

    @property
    def radius(self) -> int32:
        return self._radius

    @property
    def diameter(self) -> int32:
        return self._radius * 2

def main() -> None:
    c = Circle(5)
    print(c.radius)
    print(c.diameter)

main()
