from typing import Any, cast
class Bag:
    _data: dict[str, Any]
    def __init__(self) -> None:
        d: dict[str, Any] = {}
        self._data = d
    def __setattr__(self, name: str, value: Any) -> None:
        self._data[name] = value
    def __getattr__(self, name: str) -> Any:
        return self._data[name]
def main() -> None:
    b = Bag()
    s = "red"
    b.color = s
    print(len(b._data))
main()
