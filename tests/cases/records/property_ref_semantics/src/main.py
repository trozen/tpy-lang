# Property reference semantics: non-value types return by ref, mutation propagates
from tpy import Int32

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

class Container:
    _name: str
    _items: list[Int32]
    _pt: Point
    _big: int

    def __init__(self) -> None:
        self._name = "hello"
        self._items = [1, 2, 3]
        self._pt = Point(10, 20)
        self._big = 999999999999999999999

    @property
    def name(self) -> str:
        return self._name

    @name.setter
    def name(self, v: str) -> None:
        self._name = v

    @property
    def items(self) -> list[Int32]:
        return self._items

    @property
    def pt(self) -> Point:
        return self._pt

    @property
    def big(self) -> int:
        return self._big

def main() -> None:
    c = Container()

    # str property: read
    print(c.name)

    # list property: read + mutate through ref
    print(c.items)
    c.items.append(4)
    print(c.items)

    # record property: read + mutate through ref
    print(c.pt.x)
    c.pt.x = 99
    print(c.pt.x)

    # BigInt property: read (by ref, no copy)
    print(c.big)

    # str property on temporary: copies (no dangling view)
    s = Container().name
    print(s)

    # setter
    c.name = "world"
    print(c.name)

main()
