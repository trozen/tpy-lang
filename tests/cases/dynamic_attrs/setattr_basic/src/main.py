# D16 Phase 2: undeclared attribute writes route through __setattr__.
from typing import Any, cast

class Bag:
    _data: dict[str, Any]

    def __init__(self) -> None:
        d: dict[str, Any] = {}
        self._data = d

    def __getattr__(self, name: str) -> Any:
        return self._data[name]

    def __setattr__(self, name: str, value: Any) -> None:
        self._data[name] = value

def main() -> None:
    b = Bag()
    b.color = "red"
    b.size = 42
    print(cast(str, b.color))
    print(cast(int, b.size))

main()
