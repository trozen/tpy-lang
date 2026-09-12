# Property access on inherited classes
from tpy import int32

class Base:
    _x: int32

    def __init__(self, x: int32) -> None:
        self._x = x

    @property
    def x(self) -> int32:
        return self._x

class Child(Base):
    _y: int32

    def __init__(self, x: int32, y: int32) -> None:
        super().__init__(x)
        self._y = y

    @property
    def y(self) -> int32:
        return self._y

    def sum(self) -> int32:
        return self.x + self.y

def main() -> None:
    c = Child(10, 20)
    print(c.x)
    print(c.y)
    print(c.sum())

main()
