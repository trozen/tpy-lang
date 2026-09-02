from typing import Any, cast, Literal, overload
class Bag:
    _data: dict[str, Any]
    def __init__(self) -> None:
        d: dict[str, Any] = {}
        self._data = d
    def __setattr__[T](self, name: str, value: T) -> None:
        self._data[name] = value
def main() -> None:
    b = Bag()
    b.color = "red"
    print(len(b._data))
main()
