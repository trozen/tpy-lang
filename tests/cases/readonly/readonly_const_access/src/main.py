# readonly[T] with @readonly methods: read-only access to fields and
# readonly method calls through a readonly reference.
from tpy import int32, readonly

class Container:
    _items: list[int32]

    def __init__(self) -> None:
        self._items = [int32(10), int32(20), int32(30)]

    @readonly
    def items(self) -> list[int32]:
        return self._items

    @readonly
    def count(self) -> int32:
        return int32(len(self._items))

def read_items(c: readonly[Container]) -> None:
    items = c.items()
    print(len(items))
    print(items[0])

def main() -> None:
    c = Container()
    read_items(c)
    print(c.count())

main()
