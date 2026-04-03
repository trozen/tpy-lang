# @property getter-only (read-only property) and computed property
from tpy import Int32

class Circle:
    _radius: Int32

    def __init__(self, radius: Int32) -> None:
        self._radius = radius

    @property
    def radius(self) -> Int32:
        return self._radius

    @property
    def diameter(self) -> Int32:
        return self._radius * 2

def main() -> None:
    c = Circle(5)
    print(c.radius)
    print(c.diameter)

main()
