# Reference-type class (has list[str] field) so it's auto-inferred as a
# reference type. Construction is via the public class name, not a factory.
from tpy import Int32


class Buffer:
    _items: list[str]

    def __init__(self) -> None:
        self._items = []

    def add(self, s: str) -> None:
        self._items.append(str(s))

    def size(self) -> Int32:
        return Int32(len(self._items))

    def dump(self) -> str:
        return "".join(self._items)
