from tpy import Int32
from typing import Any
class Bag:
    _data: dict[str, Any]
    def __init__(self):
        d: dict[str, Any] = {}
        self._data = d
    def __delattr__(self, name: str) -> None:
        del self._data[name]
def drop(b: Bag) -> None:
    del b.x, b.y
def main() -> None:
    drop(Bag())
main()
