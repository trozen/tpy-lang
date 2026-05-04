# D16 Phase 1: narrow Any-typed dyn-attr returns by binding to a local first.
from typing import Any, cast

class Bag:
    _items: dict[str, Any]

    def __init__(self, items: dict[str, Any]) -> None:
        self._items = items

    def __getattr__(self, name: str) -> Any:
        return self._items[name]

def main() -> None:
    b = Bag({"label": "ready"})
    x = b.label
    if isinstance(x, str):
        print(x.upper())

main()
