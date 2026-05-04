# D16 Phase 2: setattr(obj, "declared_field", v) is rejected -- use direct assignment.
from typing import Any

class Bag:
    _data: dict[str, Any]

    def __init__(self) -> None:
        d: dict[str, Any] = {}
        self._data = d

    def __setattr__(self, name: str, value: Any) -> None:
        self._data[name] = value

def main() -> None:
    b = Bag()
    setattr(b, "_data", {})  # tpyc: error(/declared field/)

main()
