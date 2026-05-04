# D16 Phase 1: getattr(obj, "literal_name") routes through __getattr__ for undeclared names.
from typing import Any, cast

class Bag:
    _items: dict[str, Any]

    def __init__(self, items: dict[str, Any]) -> None:
        self._items = items

    def __getattr__(self, name: str) -> Any:
        return self._items[name]

def main() -> None:
    b = Bag({"alpha": "first", "beta": "second"})
    a = cast(str, getattr(b, "alpha"))
    print(a)

main()
