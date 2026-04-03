# Property access on inherited classes
from tpy import Int32

class Base:
    _x: Int32

    def __init__(self, x: Int32) -> None:
        self._x = x

    @property
    def x(self) -> Int32:
        return self._x

class Child(Base):
    _y: Int32

    def __init__(self, x: Int32, y: Int32) -> None:
        super().__init__(x)
        self._y = y

    @property
    def y(self) -> Int32:
        return self._y

    def sum(self) -> Int32:
        return self.x + self.y

def main() -> None:
    c = Child(10, 20)
    print(c.x)
    print(c.y)
    print(c.sum())

main()
