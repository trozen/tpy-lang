# readonly[T] with @readonly methods: read-only access to fields and
# readonly method calls through a readonly reference.
from tpy import Int32, readonly

class Container:
    _items: list[Int32]

    def __init__(self) -> None:
        self._items = [Int32(10), Int32(20), Int32(30)]

    @readonly
    def items(self) -> list[Int32]:
        return self._items

    @readonly
    def count(self) -> Int32:
        return Int32(len(self._items))

def read_items(c: readonly[Container]) -> None:
    items = c.items()
    print(len(items))
    print(items[0])

def main() -> None:
    c = Container()
    read_items(c)
    print(c.count())

main()
